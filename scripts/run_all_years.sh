#!/bin/bash
# Run bulk video to text transcription starting from 2026 and working backwards to 2018

# Array of years in reverse chronological order
years=(2026 2025 2024 2023 2022 2021 2020 2019 2018)

echo "Starting bulk transcription process across multiple years..."
echo "To monitor progress, tail the log file: tail -f bulk_pipeline.log"

# Export PYTHONPATH so the script can find the pipeline and services modules
export PYTHONPATH=$PYTHONPATH:$(pwd)

for year in "${years[@]}"; do
    echo "====================================="
    echo "Starting processing for year: $year"
    echo "====================================="
    
    python3 scripts/bulk_video_to_text.py --year "$year" --resume
    
    # Check if the previous command failed critically (non-zero exit status)
    if [ $? -ne 0 ]; then
        echo "🚨 Critical error occurred during processing of year $year. Stopping."
        exit 1
    fi
    
    echo "Finished processing for year $year."
    echo ""
done

echo "🎉 All years (2026-2018) have been successfully processed!"
