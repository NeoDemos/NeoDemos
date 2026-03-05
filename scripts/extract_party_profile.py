#!/usr/bin/env python3
"""
Extract GroenLinks-PvdA party profile from multiple sources.
This creates a high-confidence profile used for contextual stance detection.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.party_profile_service import PartyProfileExtractor, PartyProfile
from dataclasses import asdict

def main():
    """Extract and display party profile"""
    
    try:
        extractor = PartyProfileExtractor()
        
        # Extract GroenLinks-PvdA profile
        # Pattern matches: "groenlinks", "pvda", "partij van de arbeid"
        profile = extractor.extract_full_profile(
            party_name="GroenLinks-PvdA",
            party_pattern="groenlinks|pvda|partij van de arbeid"
        )
        
        # Convert to dict for JSON serialization
        profile_dict = asdict(profile)
        
        # Display summary
        print("\n" + "="*70)
        print("PARTY PROFILE EXTRACTED SUCCESSFULLY")
        print("="*70)
        
        print(f"\nParty: {profile_dict['party_name']}")
        print(f"Generated: {profile_dict['profile_generated_at']}")
        print(f"Overall Ideology: {profile_dict['overall_ideology']}")
        print(f"Priority Topics: {', '.join(profile_dict['priority_topics'][:5])}")
        print(f"Internal Consistency: {profile_dict['internal_consistency']:.1%}")
        print(f"Data Sources: {', '.join(profile_dict['data_sources_used'])}")
        
        print(f"\n{len(profile_dict['topic_stances'])} topic stances identified")
        
        # Save profile to JSON file
        output_file = "groenlinks_pvda_profile.json"
        with open(output_file, "w") as f:
            json.dump(profile_dict, f, indent=2, default=str)
        
        print(f"\n✓ Full profile saved to: {output_file}")
        print("\nPlease review the profile for accuracy before approving for downstream analysis.")
        
        # Show first few topic stances
        print("\nSample Topic Stances:")
        for topic_name, stance in list(profile_dict['topic_stances'].items())[:3]:
            print(f"\n  {topic_name}:")
            print(f"    Position: {stance['position'][:80]}...")
            print(f"    Direction: {stance['direction']}")
            print(f"    Confidence: {stance['confidence']:.1%}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
