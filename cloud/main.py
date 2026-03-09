import boto3
import base64
import os
import json
import cgi
import io
import logging
from datetime import datetime


def handler(event, context):
    logging.info(f"Event: {json.dumps(event)}")
    http_method = event.get("requestContext", {}).get("http", {}).get("method")
    if http_method != "POST":
        return {"statusCode": 405, "body": json.dumps({"error": "Method not allowed"})}

    body = event.get("body", "")
    if not body:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No body provided"}),
        }

    headers = event.get("headers", {})
    content_type = headers.get("content-type") or headers.get("Content-Type", "")

    if "multipart/form-data" not in content_type:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Content-Type must be multipart/form-data"}),
        }

    # Decode body if base64 (multipart can be base64 if binary parts)
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(body)
    else:
        raw_body = body.encode("utf-8")

    # Parse multipart
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw_body)),
    }
    fp = io.BytesIO(raw_body)
    form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

    image_file = form.getfirst("image")
    if image_file is None:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No image field in form"}),
        }
    image_data = image_file

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
            "body": json.dumps(
                {"message": "Image uploaded successfully", "key": key, "url": image_url}
            ),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
