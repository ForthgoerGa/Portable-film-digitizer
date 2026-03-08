import boto3
import base64
import os
import json
from datetime import datetime


def handler(event, context):
    http_method = event.get("requestContext", {}).get("httpMethod")
    if http_method != "POST":
        return {"statusCode": 405, "body": json.dumps({"error": "Method not allowed"})}

    # Assuming the image is base64 encoded in the body
    body = event.get("body", "")
    if not body:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No image data provided"}),
        }

    try:
        if event.get("isBase64Encoded"):
            image_data = base64.b64decode(body)
        else:
            # If not base64, assume it's raw bytes as string, but unlikely for image
            image_data = body.encode("utf-8")
    except Exception as e:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid data"})}

    s3 = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    key = f"uploaded_image_{timestamp}.jpg"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=image_data,
            ContentType="image/jpeg",
            ACL="public-read",  # For later viewing
        )
        image_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Image uploaded successfully", "key": key, "url": image_url}),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})})