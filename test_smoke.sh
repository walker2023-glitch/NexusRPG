#!/bin/bash

# Ensure the script exits immediately if any command fails
set -e

echo "========================================="
echo "   NEXUS ENGINE ENGINE SMOKE TEST        "
echo "========================================="
echo ""

echo "=== 1. CHECKING SYSTEM HEALTH ==="
curl -s http://localhost:8000/health | python3 -m json.tool
echo ""

echo "=== 2. TESTING ARCHITECT MODE (GRAPH EXTRACTION) ==="
curl -s -X POST http://localhost:8000/architect/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "Tony Stark is a genius billionaire who built the Iron Man suit. He is allied with Steve Rogers and lives in Stark Tower in New York.",
    "universe_context": "Marvel Cinematic Universe"
  }' | python3 -m json.tool
echo ""

echo "=== 3. VERIFYING WORLD STATE PERSISTENCE ==="
curl -s http://localhost:8000/api/world-state | python3 -m json.tool
echo ""

echo "=== 4. TESTING ACTOR MODE (RPG INITIALIZATION) ==="
curl -s -X POST http://localhost:8000/actor/scene \
  -H "Content-Type: application/json" \
  -d '{
    "player_action": "Look around the room and assess the situation.",
    "freytag_stage": "EXPOSITION",
    "parent_commit_id": null
  }' | python3 -m json.tool
echo ""

echo "=== 5. VERIFYING CHRONOS TIMELINE COMMIT ==="
curl -s http://localhost:8000/chronos/timeline | python3 -m json.tool
echo ""

echo "========================================="
echo "         SMOKE TEST COMPLETE             "
echo "========================================="