#!/usr/bin/env python3
"""
Extract full GroenLinks-PvdA party profile from all sources.
Run this script to generate the comprehensive profile for review.
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.party_profile_service import PartyProfileExtractor
from dataclasses import asdict

def main():
    """Extract and save profile"""
    
    # Ensure API key is loaded
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # Try loading from .env
        with open(".env") as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    os.environ["GEMINI_API_KEY"] = api_key
                    break
    
    if not api_key:
        print("✗ GEMINI_API_KEY not found")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print("GROENLINKS-PVDA FULL PARTY PROFILE EXTRACTION")
    print(f"{'='*70}\n")
    
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        extractor = PartyProfileExtractor()
        
        # Extract complete profile
        profile = extractor.extract_full_profile(
            party_name="GroenLinks-PvdA",
            party_pattern="groenlinks|pvda|partij van de arbeid"
        )
        
        # Convert to dict
        profile_dict = asdict(profile)
        
        # Display summary
        print("\n" + "="*70)
        print("✓ PROFILE EXTRACTION COMPLETE")
        print("="*70)
        
        print(f"\nParty: {profile_dict['party_name']}")
        print(f"Generated: {profile_dict['profile_generated_at']}")
        print(f"Overall Ideology: {profile_dict['overall_ideology']}")
        print(f"Rhetorical Tone: {profile_dict['rhetorical_tone']}")
        print(f"Voting Pattern: {profile_dict['voting_pattern']}")
        print(f"\nPriority Topics ({len(profile_dict['priority_topics'])}):")
        for i, topic in enumerate(profile_dict['priority_topics'], 1):
            print(f"  {i}. {topic}")
        
        print(f"\nInternal Consistency: {profile_dict['internal_consistency']:.1%}")
        print(f"Data Sources: {', '.join(profile_dict['data_sources_used'])}")
        
        print(f"\nKey Distinguishing Positions:")
        for i, pos in enumerate(profile_dict['key_distinguishing_positions'][:5], 1):
            print(f"  {i}. {pos}")
        
        print(f"\nTopic Stances: {len(profile_dict['topic_stances'])} identified")
        
        # Save to JSON
        output_file = "groenlinks_pvda_full_profile.json"
        with open(output_file, "w") as f:
            json.dump(profile_dict, f, indent=2, default=str)
        
        print(f"\n✓ Complete profile saved to: {output_file}")
        print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return True
    
    except Exception as e:
        print(f"\n✗ Error during profile extraction: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
