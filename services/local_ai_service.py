import os
import logging
import hashlib
from collections import OrderedDict
from typing import Optional, List, Dict, Any
from pathlib import Path

# LRU cache for generated embeddings — avoids re-embedding identical queries
# Key: MD5 hex of input text. Value: tuple of floats (immutable, safe to share).
_EMBED_CACHE: OrderedDict = OrderedDict()
_EMBED_CACHE_MAX = 512

try:
    from mlx_lm import load, generate
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

logger = logging.getLogger(__name__)

_GLOBAL_MODEL_CACHE = None
_GLOBAL_TOKENIZER_CACHE = None
_GLOBAL_EMBED_CACHE = None

class LocalAIService:
    """
    Provides local LLM inference using MLX on Apple Silicon.
    Designed to fall back to Gemini if the local model is not found.
    """
    def __init__(self, model_path: Optional[str] = None, skip_llm: bool = False):
        global _GLOBAL_MODEL_CACHE, _GLOBAL_TOKENIZER_CACHE, _GLOBAL_EMBED_CACHE
        
        self.skip_llm = skip_llm
        self.model = _GLOBAL_MODEL_CACHE
        self.tokenizer = _GLOBAL_TOKENIZER_CACHE
        self.embed_model = None
        self.embed_tokenizer = None
        self.use_local = False
        self.use_embed = False
        
        if _GLOBAL_EMBED_CACHE:
            self.embed_model, self.embed_tokenizer = _GLOBAL_EMBED_CACHE

        # Load LLM (Only if not skipped)
        if not skip_llm:
            # Default to the Mistral model in LM Studio
            if model_path is None:
                home = str(Path.home())
                model_path = os.path.join(home, ".lmstudio/models/lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit")
            
            self.model_path = model_path
            
            if MLX_AVAILABLE and os.path.exists(self.model_path):
                if self.model is None or self.tokenizer is None:
                    try:
                        logger.info(f"Loading local MLX model from: {self.model_path}")
                        self.model, self.tokenizer = load(self.model_path)
                        _GLOBAL_MODEL_CACHE = self.model
                        _GLOBAL_TOKENIZER_CACHE = self.tokenizer
                        self.use_local = True
                        logger.info("✅ Local MLX model loaded successfully.")
                    except Exception as e:
                        logger.error(f"❌ Failed to load local MLX model: {e}")
                else:
                    self.use_local = True
                    logger.info("⚡ Using already loaded MLX model from cache.")
        else:
            self.model_path = None
            logger.info("ℹ️ skip_llm=True: Skipping 24B Mistral initialization to save RAM.")
        
        # Load Embedding Model
        if MLX_AVAILABLE:
            if self.embed_model is None:
                try:
                    logger.info("Loading local high-precision embedding model (MLX): Qwen3-Embedding-8B-4bit-DWQ")
                    model_path = os.path.expanduser("~/.lmstudio/models/mlx-community/Qwen3-Embedding-8B-4bit-DWQ")
                    
                    if not os.path.exists(model_path):
                        raise FileNotFoundError(f"Model path not found: {model_path}")
                    
                    # Import here to avoid scoping issues later
                    from mlx_lm import load as mlx_load
                    self.embed_model, self.embed_tokenizer = mlx_load(model_path)
                    _GLOBAL_EMBED_CACHE = (self.embed_model, self.embed_tokenizer)
                    self.use_embed = True
                    logger.info("✅ Qwen3-8B embedding model (MLX) loaded successfully.")
                except Exception as e:
                    logger.error(f"❌ Failed to load local embedding model: {e}")
            else:
                self.embed_model, self.embed_tokenizer = _GLOBAL_EMBED_CACHE
                self.use_embed = True
                logger.info("⚡ Using already loaded embedding model and tokenizer from cache.")
        else:
            if not MLX_AVAILABLE:
                logger.warning("mlx-lm not installed. Local inference unavailable.")
            if not os.path.exists(self.model_path):
                logger.warning(f"Local model path not found: {self.model_path}")

    def generate_content(self, prompt: str, max_tokens: int = 8192) -> str:
        """
        Generates text using the local model.
        Returns empty string on failure.
        """
        if not self.use_local:
            return ""

        try:
            # Use chat template if available, otherwise raw prompt
            if hasattr(self.tokenizer, "apply_chat_template"):
                messages = [{"role": "user", "content": prompt}]
                formatted_prompt = self.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                formatted_prompt = prompt

            response = generate(
                self.model, 
                self.tokenizer, 
                prompt=formatted_prompt, 
                max_tokens=max_tokens
            )
            return response
        except Exception as e:
            logger.error(f"Local inference error: {e}")
            return ""
        finally:
            if MLX_AVAILABLE and self.use_local:
                import mlx.core as mx
                mx.clear_cache()

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generates a vector embedding using the local MLX model (Mistral 4096-dim or Qwen3-Embedding).
        Results are cached in-process (LRU, max 512 entries) to avoid re-embedding identical queries.
        """
        if not self.use_embed and not self.use_local:
            return None

        # Check LRU cache before hitting the GPU
        cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
        if cache_key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(cache_key)
            return list(_EMBED_CACHE[cache_key])

        # Use main model if specific embedding model is missing
        # REFAC: Remove fallback to LLM model for embeddings to protect RAM
        target_model = self.embed_model
        target_tokenizer = self.embed_tokenizer

        if target_model is None or target_tokenizer is None:
            logger.error("❌ Embedding model not loaded. Returning None.")
            return None

        try:
            import mlx.core as mx

            # The mlx_lm tokenizer is usually a TokenizerWrapper.
            input_ids = mx.array(target_tokenizer.encode(text))[None] # Add batch dim

            # Use model metadata to find hidden states if it's a generator wrapper
            # For Mistral/MLX, calling the model object often returns logits.
            # We want the LAST HIDDEN STATE.
            if hasattr(target_model, "model"):
                 # Handle wrapper if present (common in mlx-lm)
                 # We avoid full generation and just do one forward pass
                 hidden_states = target_model.model(input_ids)
            else:
                 hidden_states = target_model(input_ids)

            # Mean pooling across the sequence dimension (axis 1)
            # Mistral hidden states are [Batch, Seq, Dim]
            # We take the mean of the sequence to get a single vector.
            if hasattr(hidden_states, "shape") and len(hidden_states.shape) == 3:
                embedding = mx.mean(hidden_states, axis=1)
            else:
                # If it's already pooled or different shape
                embedding = mx.mean(hidden_states, axis=0) if len(hidden_states.shape) == 2 else hidden_states

            # Normalize for cosine similarity
            norm = mx.linalg.norm(embedding, axis=-1, keepdims=True)
            normalized_embedding_tensor = (embedding / (norm + 1e-9))
            
            # --- CRITICAL: STRICTOR EVALUATION FOR RAM SAFETY ---
            mx.eval(normalized_embedding_tensor)
            normalized_embedding = normalized_embedding_tensor.tolist()[0]
            
            # Explicitly clear intermediate arrays from VRAM
            mx.clear_cache()

            # ENSURE 4096 DIMENSION (Log error if wrong)
            if len(normalized_embedding) != 4096:
                logger.warning(f"Vector dim mismatch: expected 4096, got {len(normalized_embedding)}")

            # Store in LRU cache (evict oldest if full)
            _EMBED_CACHE[cache_key] = tuple(normalized_embedding)
            if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.popitem(last=False)

            return normalized_embedding
        except Exception as e:
            logger.error(f"Local embedding error: {e}")
            return None
        finally:
            # Removed mx.clear_cache() from here because it's handled at the batch level 
            # or caller level to prevent frequent GPU descriptor re-allocations.
            pass

    def _embed_single_masked(self, text: str) -> Optional[List[float]]:
        """Embed a single text with masked pooling. Used as fallback for NaN results."""
        try:
            import mlx.core as mx
            target_model = self.embed_model
            target_tokenizer = self.embed_tokenizer

            token_ids = target_tokenizer.encode(text[:50000])
            input_ids = mx.array([token_ids])

            if hasattr(target_model, "model"):
                hidden_states = target_model.model(input_ids)
            else:
                hidden_states = target_model(input_ids)

            # No padding, so just mean over the full sequence
            embedding = mx.mean(hidden_states, axis=1)
            norm = mx.linalg.norm(embedding, axis=-1, keepdims=True)
            normalized = embedding / mx.maximum(norm, mx.array(1e-9))

            mx.eval(normalized)
            result = normalized.tolist()[0]

            del hidden_states, embedding, normalized, input_ids
            mx.clear_cache()

            return result if len(result) == 4096 else None
        except Exception as e:
            logger.debug(f"Single masked embed failed: {e}")
            return None

    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Hybrid approach: fast naive mean-pooling for the batch, then masked
        single-item retry for any results that come back as NaN/Inf.
        """
        if not texts:
            return []

        if not self.use_embed:
            return []

        target_model = self.embed_model
        target_tokenizer = self.embed_tokenizer

        if target_model is None or target_tokenizer is None:
            return []

        try:
            import mlx.core as mx
            import numpy as np

            max_chars = 50000
            truncated_texts = [(t[:max_chars] if len(t) > max_chars else t) for t in texts]

            # Tokenize
            token_ids = [target_tokenizer.encode(t) for t in truncated_texts]
            max_len = max(len(t) for t in token_ids)

            pad_id = getattr(target_tokenizer, "pad_token_id", 0)
            if pad_id is None:
                pad_id = 0

            # Pad and run single GPU pass (fast)
            padded = [t + [pad_id] * (max_len - len(t)) for t in token_ids]
            input_ids = mx.array(padded)

            if hasattr(target_model, "model"):
                hidden_states = target_model.model(input_ids)
            else:
                hidden_states = target_model(input_ids)

            embeddings = mx.mean(hidden_states, axis=1)
            norms = mx.linalg.norm(embeddings, axis=-1, keepdims=True)
            normalized = embeddings / (norms + 1e-9)

            mx.eval(normalized)
            results = normalized.tolist()

            del hidden_states, embeddings, normalized, input_ids
            mx.clear_cache()

            # Check for NaN/Inf and retry those individually with masked pooling
            nan_indices = []
            for i, vec in enumerate(results):
                if vec and (any(np.isnan(vec)) or any(np.isinf(vec))):
                    nan_indices.append(i)

            if nan_indices:
                logger.info(f"Retrying {len(nan_indices)}/{len(texts)} NaN embeddings with masked pooling")
                for idx in nan_indices:
                    retry = self._embed_single_masked(truncated_texts[idx])
                    if retry and not any(np.isnan(retry)) and not any(np.isinf(retry)):
                        results[idx] = retry
                    else:
                        results[idx] = None  # will be skipped by caller

            if results and results[0] and len(results[0]) != 4096:
                logger.warning(f"Batch Vector dim mismatch: expected 4096, got {len(results[0])}")

            return results
        except Exception as e:
            logger.error(f"Batch embedding error: {str(e)}")
            return []
        finally:
            if MLX_AVAILABLE:
                import mlx.core as mx
                mx.clear_cache()

    def is_available(self) -> bool:
        return self.use_local or self.use_embed

def manual_memory_reset():
    """Manual trigger to clear global model caches before a heavy run."""
    global _GLOBAL_MODEL_CACHE, _GLOBAL_TOKENIZER_CACHE, _GLOBAL_EMBED_CACHE
    import gc
    try:
        import mlx.core as mx
        mx.clear_cache()
    except:
        pass
    _GLOBAL_MODEL_CACHE = None
    _GLOBAL_TOKENIZER_CACHE = None
    _GLOBAL_EMBED_CACHE = None
    gc.collect()
    logger.info("♻️ Global AI Models cleared from Unified Memory cache.")
