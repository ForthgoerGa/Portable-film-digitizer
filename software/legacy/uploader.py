import json
import requests
from typing import Optional

# Configurable API endpoint
CLOUD_API_ENDPOINT = "https://rduugzdncip2vlpu54o53jhbay0psidi.lambda-url.us-east-2.on.aws"


class UploadError(Exception):
    """Raised when image upload to cloud API fails."""

    pass


class Uploader:
    """Handles uploading stitched images to the cloud API."""

    def upload(self, image_bytes: bytes) -> str:
        """
        Uploads the stitched image bytes to the cloud API.

        Args:
            image_bytes: The image data as bytes.

        Returns:
            The cloud URL of the uploaded image.

        Raises:
            UploadError: If upload fails or API returns error.
        """
        try:
            files = {"image": ("stitch.jpg", image_bytes, "image/jpeg")}
            response = requests.post(CLOUD_API_ENDPOINT, files=files)
            response.raise_for_status()  # Raise for HTTP errors

            data = response.json()
            url = data.get("url")
            error = data.get("error")

            if url:
                return url
            else:
                raise UploadError(f"API returned error: {error}")

        except requests.RequestException as e:
            raise UploadError(f"Upload request failed: {e}")
        except json.JSONDecodeError as e:
            raise UploadError(f"Invalid JSON response: {e}")
