#!/bin/bash
# Interview Document Generator
# Usage (from any directory):
#   bash docs/tools/generate_interview.sh FR001 "Feature Title" ["Project Name"]
# Output: docs/interview/FR001_interview_<title>.docx

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <FR_NUMBER> <FEATURE_TITLE> [PROJECT_NAME]"
  echo "Example: $0 FR001 'User Authentication' 'mini-sdv-platform'"
  exit 1
fi

FR_NUMBER=$1
FEATURE_TITLE=$2
PROJECT_NAME=${3:-"mini-sdv-platform"}

# Resolve paths relative to this script — works from any working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/interview"

mkdir -p "$OUTPUT_DIR"

python "$SCRIPT_DIR/generate_interview_py.py" "$FR_NUMBER" "$FEATURE_TITLE" "$PROJECT_NAME" "$OUTPUT_DIR"
