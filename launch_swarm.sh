#!/bin/bash
echo "Starting 20-worker Phase B Swarm..."
# Years to process in parallel
YEARS=(2026 2025 2024 2023 2022 2021 2020 2019 2018 2017 2016 2015 2014 2013 2012 2011 2010 2009 2008 2007)

mkdir -p logs

for y in "${YEARS[@]}"; do
    echo "Launching worker for $y..."
    nohup /Users/dennistak/Documents/Final\ Frontier/NeoDemos/.venv/bin/python3 -u scripts/compute_embeddings.py --year $y > "logs/chunking_$y.log" 2>&1 &
    sleep 0.5  # Stagger startup slightly
done

echo "Swarm launched! Monitor with: tail -f logs/chunking_*.log"
