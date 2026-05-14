"""Utilities for handling LLM API errors (Anthropic and OpenAI)."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class APIErrorResult:
    """Result from handling an API error."""

    user_message: str
    is_retryable: bool
    log_level: int = logging.ERROR


def handle_llm_error(error: Exception) -> APIErrorResult:
    """Handle LLM API errors (Anthropic or OpenAI) with appropriate messages.

    Args:
        error: The exception raised by an LLM client.

    Returns:
        APIErrorResult with user-friendly message and metadata.
    """
    error_type = type(error).__name__

    # Try to handle Anthropic-specific errors
    try:
        import anthropic

        if isinstance(error, anthropic.RateLimitError):
            logger.warning(f"Anthropic rate limit hit: {error}")
            return APIErrorResult(
                user_message="I'm being rate limited. Please wait a moment and try again.",
                is_retryable=True,
                log_level=logging.WARNING,
            )

        if isinstance(error, anthropic.AuthenticationError):
            logger.error(f"Anthropic authentication error: {error}")
            return APIErrorResult(
                user_message="API authentication failed. Please check the ANTHROPIC_API_KEY configuration.",
                is_retryable=False,
                log_level=logging.ERROR,
            )

        if isinstance(error, anthropic.NotFoundError):
            logger.error(f"Anthropic model not found: {error}")
            return APIErrorResult(
                user_message=(
                    "The configured AI model is no longer available. "
                    "Use /model to switch to a different model, "
                    "or update the model family (sonnet, haiku, opus) in your config."
                ),
                is_retryable=False,
                log_level=logging.ERROR,
            )

        if isinstance(error, anthropic.BadRequestError):
            logger.error(f"Anthropic bad request: {error}")
            return APIErrorResult(
                user_message="Invalid request to AI service. This may be a bug.",
                is_retryable=False,
                log_level=logging.ERROR,
            )

        if isinstance(error, anthropic.APIConnectionError):
            logger.warning(f"Anthropic connection error: {error}")
            return APIErrorResult(
                user_message="Couldn't connect to AI service. Please try again later.",
                is_retryable=True,
                log_level=logging.WARNING,
            )

        if isinstance(error, anthropic.APIStatusError):
            logger.error(f"Anthropic API status error: {error}")
            return APIErrorResult(
                user_message="AI service returned an error. Please try again later.",
                is_retryable=True,
                log_level=logging.ERROR,
            )

        if isinstance(error, anthropic.APIError):
            logger.error(f"Anthropic API error: {error}")
            return APIErrorResult(
                user_message="AI service error. Please try again later.",
                is_retryable=True,
                log_level=logging.ERROR,
            )

    except ImportError:
        # anthropic module not available, fall through to next handler
        pass

    # Try to handle OpenAI-specific errors
    try:
        import openai

        if isinstance(error, openai.RateLimitError):
            logger.warning(f"OpenAI rate limit hit: {error}")
            return APIErrorResult(
                user_message="I'm being rate limited. Please wait a moment and try again.",
                is_retryable=True,
                log_level=logging.WARNING,
            )

        if isinstance(error, openai.AuthenticationError):
            logger.error(f"OpenAI authentication error: {error}")
            return APIErrorResult(
                user_message="API authentication failed. Please check the API key configuration.",
                is_retryable=False,
                log_level=logging.ERROR,
            )

        if isinstance(error, openai.BadRequestError):
            logger.error(f"OpenAI bad request: {error}")
            return APIErrorResult(
                user_message="Invalid request to AI service. This may be a bug.",
                is_retryable=False,
                log_level=logging.ERROR,
            )

        if isinstance(error, openai.APIConnectionError):
            logger.warning(f"OpenAI connection error: {error}")
            return APIErrorResult(
                user_message="Couldn't connect to AI service. Please try again later.",
                is_retryable=True,
                log_level=logging.WARNING,
            )

        if isinstance(error, openai.APIStatusError):
            logger.error(f"OpenAI API status error: {error}")
            return APIErrorResult(
                user_message="AI service returned an error. Please try again later.",
                is_retryable=True,
                log_level=logging.ERROR,
            )

        if isinstance(error, openai.APIError):
            logger.error(f"OpenAI API error: {error}")
            return APIErrorResult(
                user_message="AI service error. Please try again later.",
                is_retryable=True,
                log_level=logging.ERROR,
            )

    except ImportError:
        # openai module not available, fall through to generic handling
        pass

    # Generic error handling
    logger.error(f"Unexpected error during API call ({error_type}): {error}")
    return APIErrorResult(
        user_message=f"An unexpected error occurred: {error_type}",
        is_retryable=False,
        log_level=logging.ERROR,
    )
