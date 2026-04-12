import httpx
import io
import re
from pypdf import PdfReader
from typing import Optional

# Pre-compiled patterns — strip these before measuring space density
_STRIP_URL_RE      = re.compile(r'https?://\S+', re.I)
_STRIP_FILENAME_RE = re.compile(r'\S+\.(?:pdf|docx?|xlsx?|txt|xml|json|csv|png|jpg)\b', re.I)
_STRIP_PATH_RE     = re.compile(r'(?<!\w)/[\w/.\-_]{15,}')
_STRIP_REFCODE_RE  = re.compile(r'\b(?:[A-Z]{2,}[\-_]){2,}[A-Z0-9]+\b|\b[A-Z0-9]{20,}\b')
_GARBLED_RUN_RE    = re.compile(r'[^\s]{35,}')

def _is_garbled_ocr(text: str) -> bool:
    """Detect word-concatenation artifacts from broken PDF text layers.

    Returns True when pypdf faithfully reproduced a corrupted text layer
    (words merged without spaces, e.g. "DegemeenteraadvanRotterdam...").

    Safe against false positives from URLs, file paths, reference codes,
    and legitimate Dutch compound words (max ~30 chars).
    """
    if not text or len(text.strip()) < 50:
        return False
    # Strip non-prose tokens before measuring space density
    prose = _STRIP_URL_RE.sub(' ', text)
    prose = _STRIP_FILENAME_RE.sub(' ', prose)
    prose = _STRIP_PATH_RE.sub(' ', prose)
    prose = _STRIP_REFCODE_RE.sub(' ', prose)
    prose = prose.strip()
    if len(prose) < 20:
        return False  # Entire text was URLs/refs — can't assess
    space_ratio   = prose.count(' ') / len(prose)
    garbled_runs  = _GARBLED_RUN_RE.findall(prose)
    garbled_frac  = sum(len(r) for r in garbled_runs) / len(prose)
    return (
        (space_ratio < 0.06 and garbled_frac > 0.05)
        or garbled_frac > 0.20
        or (space_ratio < 0.10 and garbled_frac > 0.10)
    )

class ScraperService:
    async def extract_text_from_url(self, url: str) -> Optional[str]:
        """Downloads a PDF from a URL and extracts its text with native macOS OCR fallback."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                
                # Try simple PDF text extraction first
                pdf_file = io.BytesIO(response.content)
                reader = PdfReader(pdf_file)
                
                full_text = ""
                for page in reader.pages:
                    full_text += (page.extract_text() or "") + "\n"
                
                full_text = full_text.strip()
                
                # Trigger OCR if text is absent OR if the text layer is garbled
                if len(full_text) < 200 or _is_garbled_ocr(full_text):
                    import subprocess
                    import tempfile
                    import os

                    reason = "low text" if len(full_text) < 200 else "garbled text layer"
                    print(f"{reason} detected ({len(full_text)} chars), triggering native OCR fallback...")
                    
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(response.content)
                        tmp_path = tmp.name
                    
                    try:
                        # Path to our compiled Swift OCR tool
                        ocr_tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ocr_pdf")
                        if os.path.exists(ocr_tool):
                            result = subprocess.run([ocr_tool, tmp_path], capture_output=True, text=True, timeout=120)
                            if result.returncode == 0 and result.stdout:
                                # Clean up debug prints from Swift output
                                ocr_text = result.stdout
                                if "--- OCR RESULT START ---" in ocr_text:
                                    ocr_text = ocr_text.split("--- OCR RESULT START ---")[1].split("--- OCR RESULT END ---")[0]
                                full_text = ocr_text.strip()
                                print(f"✓ Native OCR success: {len(full_text)} chars extracted.")
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                
                return full_text if full_text else None
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            return None

    def compress_text(self, text: str, max_length: Optional[int] = 15000) -> str:
        """
        Preserve document content while removing formatting clutter.
        Goal: Keep full semantic content for deep analysis, removing only:
        - Excessive whitespace
        - Page breaks
        - Repetitive headers
        
        Args:
            text: The text to compress
            max_length: Maximum character limit. Default 15,000 for summaries.
                       Use None for full preservation (notulen analysis).
        
        Council members need the full context for proper decision-making.
        Limit: 15,000 characters preserves ~3-4 pages of single-spaced text.
        """
        if not text: return ""
        
        lines = text.split('\n')
        cleaned = []
        
        previous_was_blank = False
        for line in lines:
            line = line.rstrip()
            
            # Skip multiple consecutive blank lines
            if not line.strip():
                if not previous_was_blank:
                    cleaned.append('')
                    previous_was_blank = True
                continue
            
            previous_was_blank = False
            
            # Skip common footer/header patterns
            if any(pattern in line.lower() for pattern in [
                'pagina', 'page ', 'bladzijde', '- -', '___', '---'
            ]):
                continue
            
            # Skip very short fragments (likely formatting artifacts)
            if len(line.strip()) < 2:
                continue
                
            cleaned.append(line)
        
        # Join and apply length limit if specified
        result = "\n".join(cleaned)
        if max_length is not None:
            return result[:max_length]
        return result
    
    def preserve_notulen_text(self, text: str) -> str:
        """
        Preserve full notulen content without truncation.
        Uses same formatting cleanup as compress_text() but preserves full content.
        """
        return self.compress_text(text, max_length=None)
