#!/usr/bin/env python3
"""
Test script to verify calendar and homepage pages render correctly.

This prevents regressions where:
1. HTML entities (&#34;) appear inside <script> tags, breaking JS
2. Expected DOM elements are missing
3. Data is not being passed to JavaScript correctly
"""

import requests
import time
import subprocess
import os
import signal
import sys
import json
import re


def start_server():
    """Start the FastAPI server in the background."""
    print("Starting FastAPI server...")
    server_process = subprocess.Popen(
        ["python", "main.py"],
        cwd="/Users/dennistak/Documents/Final Frontier/NeoDemos",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(3)  # Give server time to start
    return server_process


def stop_server(process):
    """Stop the FastAPI server."""
    print("Stopping server...")
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    process.wait(timeout=5)


def test_calendar_page():
    """Test the /calendar page for common issues."""
    print("\n" + "=" * 60)
    print("TESTING CALENDAR PAGE")
    print("=" * 60)
    
    try:
        response = requests.get("http://localhost:8000/calendar", timeout=5)
        html = response.text
        
        # Test 1: Check for HTML entity escaping in <script> tags
        print("\n1. Checking for HTML entity escaping in JavaScript...")
        script_match = re.search(r'<script>(.*?)const meetings = (.*?);</script>', html, re.DOTALL)
        
        if script_match:
            js_section = script_match.group(2)
            if '&#34;' in js_section or '&#39;' in js_section:
                print("   ❌ FAIL: HTML entities found in JavaScript")
                print(f"      Found: {js_section[:100]}...")
                return False
            print("   ✓ PASS: No HTML entities in JavaScript")
        else:
            print("   ⚠ WARNING: Could not find <script>const meetings in page")
        
        # Test 2: Check that JSON is valid
        print("\n2. Checking if meetings JSON is valid...")
        json_match = re.search(r'const meetings = (\[.*?\]);', html, re.DOTALL)
        
        if json_match:
            json_str = json_match.group(1)
            try:
                meetings = json.loads(json_str)
                print(f"   ✓ PASS: Valid JSON with {len(meetings)} meetings")
            except json.JSONDecodeError as e:
                print(f"   ❌ FAIL: Invalid JSON - {e}")
                return False
        else:
            print("   ❌ FAIL: Could not find meetings JSON in page")
            return False
        
        # Test 3: Check for calendar grid container
        print("\n3. Checking for calendar grid container...")
        if 'id="calendar-grid-container"' in html:
            print("   ✓ PASS: Calendar grid container found")
        else:
            print("   ❌ FAIL: Calendar grid container not found")
            return False
        
        # Test 4: Check for navigation buttons
        print("\n4. Checking for navigation buttons...")
        if 'id="btn-prev"' in html and 'id="btn-next"' in html:
            print("   ✓ PASS: Navigation buttons found")
        else:
            print("   ❌ FAIL: Navigation buttons not found")
            return False
        
        # Test 5: Check for renderCalendar function
        print("\n5. Checking for renderCalendar function...")
        if 'function renderCalendar()' in html:
            print("   ✓ PASS: renderCalendar function defined")
        else:
            print("   ❌ FAIL: renderCalendar function not found")
            return False
        
        # Test 6: Check for changeMonth function
        print("\n6. Checking for changeMonth function...")
        if 'function changeMonth(delta)' in html:
            print("   ✓ PASS: changeMonth function defined")
        else:
            print("   ❌ FAIL: changeMonth function not found")
            return False
        
        print("\n✓ CALENDAR PAGE: All tests passed")
        return True
        
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to server at http://localhost:8000")
        return False
    except Exception as e:
        print(f"❌ Error testing calendar page: {e}")
        return False


def test_homepage():
    """Test the / (Overzicht) page for common issues."""
    print("\n" + "=" * 60)
    print("TESTING HOMEPAGE (OVERZICHT)")
    print("=" * 60)
    
    try:
        response = requests.get("http://localhost:8000/", timeout=5)
        html = response.text
        
        # Test 1: Check for table
        print("\n1. Checking for meetings table...")
        if 'class="meetings-table"' in html:
            print("   ✓ PASS: Meetings table found")
        else:
            print("   ❌ FAIL: Meetings table not found")
            return False
        
        # Test 2: Check for table rows with data attributes
        print("\n2. Checking for table rows with data-meeting-id...")
        if 'data-meeting-id=' in html:
            count = html.count('data-meeting-id=')
            print(f"   ✓ PASS: Found {count} meeting rows")
        else:
            print("   ❌ FAIL: No rows with data-meeting-id attribute")
            return False
        
        # Test 3: Check for alternating background colors
        print("\n3. Checking for alternating row colors...")
        if 'background-color: #ffffff' in html and 'background-color: #f5f7fa' in html:
            print("   ✓ PASS: Alternating row colors found")
        else:
            print("   ❌ FAIL: Row color styling not found")
            return False
        
        # Test 4: Check for date formatting function
        print("\n4. Checking for date formatting function...")
        if 'function formatDateWithSmallCaps(dateStr)' in html:
            print("   ✓ PASS: Date formatting function found")
        else:
            print("   ❌ FAIL: Date formatting function not found")
            return False
        
        # Test 5: Check for row click handlers
        print("\n5. Checking for row click handlers...")
        if 'row.addEventListener(\'click\'' in html:
            print("   ✓ PASS: Row click event listeners found")
        else:
            print("   ❌ FAIL: Row click event listeners not found")
            return False
        
        print("\n✓ HOMEPAGE: All tests passed")
        return True
        
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to server at http://localhost:8000")
        return False
    except Exception as e:
        print(f"❌ Error testing homepage: {e}")
        return False


def main():
    """Run all tests."""
    print("NeoDemos Page Verification Tests")
    print("=" * 60)
    
    # Kill any existing server
    os.system("lsof -i :8000 | grep LISTEN | awk '{print $2}' | xargs -r kill -9 2>/dev/null")
    time.sleep(1)
    
    server_process = None
    try:
        server_process = start_server()
        
        calendar_pass = test_calendar_page()
        homepage_pass = test_homepage()
        
        # Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Calendar page: {'✓ PASS' if calendar_pass else '❌ FAIL'}")
        print(f"Homepage page: {'✓ PASS' if homepage_pass else '❌ FAIL'}")
        
        if calendar_pass and homepage_pass:
            print("\n✓ All tests passed!")
            return 0
        else:
            print("\n❌ Some tests failed")
            return 1
            
    finally:
        if server_process:
            stop_server(server_process)


if __name__ == '__main__':
    sys.exit(main())
