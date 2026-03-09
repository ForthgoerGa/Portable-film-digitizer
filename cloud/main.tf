terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

resource "aws_s3_bucket" "image_bucket" {
  bucket = "film-digitizer-images-${random_string.bucket_suffix.result}"
}

resource "aws_s3_bucket_ownership_controls" "image_bucket" {
  bucket = aws_s3_bucket.image_bucket.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_public_access_block" "image_bucket" {
  bucket = aws_s3_bucket.image_bucket.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "random_string" "bucket_suffix" {
  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/main.py"
  output_path = "${path.module}/lambda.zip"
}

# create IAM identity 
resource "aws_iam_role" "lambda_exec" {
  name = "digitizer_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# attach cloudwatch access policy
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# attach S3 full access policy
resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

# lambda definition
resource "aws_lambda_function" "processor" {
  filename      = data.archive_file.lambda_zip.output_path
  function_name = "digitizer_post_processing"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "main.handler"
  runtime       = "python3.12"

  # This tells AWS to update the code whenever the ZIP hash changes
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  # adjust if too small
  timeout     = 30
  memory_size = 512

  environment {
    variables = {
      S3_BUCKET = aws_s3_bucket.image_bucket.bucket
    }
  }
}

# unsecured HTTP endpoint for testing
resource "aws_lambda_function_url" "endpoint" {
  function_name      = aws_lambda_function.processor.function_name
  authorization_type = "NONE" # public for dev; we can secure it later
}

resource "aws_lambda_permission" "allow_public_access" {
  statement_id  = "AllowExecutionFromPublicURL"
  action        = "lambda:InvokeFunctionUrl"
  function_name = aws_lambda_function.processor.function_name
  principal     = "*"

  # This matches the auth type we set in the URL resource
  function_url_auth_type = "NONE"
}

# New requirement for 2025/2026: Direct invocation permission via URL
resource "aws_lambda_permission" "allow_invoke_function" {
  statement_id  = "AllowInvokeFunctionViaURL"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "*"

  source_account = null
}

# print url to console for testing
# This will print the URL in your terminal after you run 'terraform apply'
output "public_url" {
  value = aws_lambda_function_url.endpoint.function_url
}
