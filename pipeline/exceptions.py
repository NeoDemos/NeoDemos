class PipelineError(Exception):
    """Base exception for all pipeline errors."""
    pass

class MeetingUnavailableError(PipelineError):
    """Raised when a meeting is publicly listed but inaccessible (e.g. 403 Forbidden)."""
    pass

class MeetingCancelledError(PipelineError):
    """Raised when a meeting is explicitly marked as Cancelled (Vervallen)."""
    pass

class VideoUnavailableError(PipelineError):
    """Raised when metadata exists but no MP4 download link is provided."""
    pass

class WebcastCodeExtractionError(PipelineError):
    """Raised when the Royalcast code cannot be found on an iBabs page."""
    pass
