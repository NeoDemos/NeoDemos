import os
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path

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
        
        # Default to the Mistral model in LM Studio
        if model_path is None:
            home = str(Path.home())
            model_path = os.path.join(home, ".lmstudio/models/lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit")
        
        self.model_path = model_path
        self.model = _GLOBAL_MODEL_CACHE
        self.tokenizer = _GLOBAL_TOKENIZER_CACHE
        self.embed_model = _GLOBAL_EMBED_CACHE
        self.use_local = False
        self.use_embed = False
        self.skip_llm = skip_llm

        # Load LLM (Only if not skipped)
        if not skip_llm and MLX_AVAILABLE and os.path.exists(self.model_path):
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
        
        # Load Embedding Model
        if MLX_AVAILABLE:
            if self.embed_model is None:
                try:
                    logger.info("Loading local high-precision embedding model (MLX): Qwen3-Embedding-8B-MLX")
                    model_path = os.path.expanduser("~/.lmstudio/models/Qwen/Qwen3-Embedding-8B-MLX")
                    
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
        """
        if not self.use_embed and not self.use_local:
            return None

        # Use main model if specific embedding model is missing
        target_model = self.embed_model if self.use_embed else self.model
        target_tokenizer = self.embed_tokenizer if self.use_embed else self.tokenizer

        if target_model is None or target_tokenizer is None:
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
            normalized_embedding = (embedding / (norm + 1e-9)).tolist()[0]
            
            # ENSURE 4096 DIMENSION (Log error if wrong)
            if len(normalized_embedding) != 4096:
                logger.warning(f"Vector dim mismatch: expected 4096, got {len(normalized_embedding)}")

            return normalized_embedding
        except Exception as e:
            logger.error(f"Local embedding error: {e}")
            return None
        finally:
            if MLX_AVAILABLE:
                import mlx.core as mx
                mx.clear_cache()

    def is_available(self) -> bool:
        return self.use_local or self.use_embed
