#!/usr/bin/env python3
"""
Test runner script for tiny-cache project.

This script provides an easy way to run different types of tests
and handles virtual environment activation reminders.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def check_venv():
    """Check if virtual environment is activated."""
    if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("WARNING: Virtual environment is not activated!")
        print("   Please run: . ./venv/bin/activate")
        print("   Then install dependencies: pip install -r requirements.txt")
        return False
    return True


def check_dependencies():
    """Check if required dependencies are installed."""
    try:
        import pytest
        return True
    except ImportError:
        print("ERROR: pytest is not installed!")
        print("   Please run: pip install -r requirements-dev.txt")
        return False


def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"RUNNING: {description}")
    print(f"   Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("SUCCESS!")
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FAILED with exit code {e.returncode}")
        if e.stdout:
            print("STDOUT:", e.stdout)
        if e.stderr:
            print("STDERR:", e.stderr)
        return False
    except FileNotFoundError:
        print("ERROR: pytest not found! Make sure it's installed and virtual environment is activated.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Run tests for tiny-cache project")
    parser.add_argument("--unit", action="store_true", help="Run only unit tests")
    parser.add_argument("--integration", action="store_true", help="Run only integration tests")
    parser.add_argument("--mock", action="store_true", help="Run only mocked gRPC tests")
    parser.add_argument("--fast", action="store_true", help="Run only fast tests (exclude slow)")
    parser.add_argument("--coverage", action="store_true", help="Run with coverage report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--file", help="Run specific test file")
    parser.add_argument("--test", help="Run specific test method")
    
    args = parser.parse_args()
    
    print("tiny-cache Test Runner")
    print("=" * 50)
    
    # Check environment
    if not check_venv():
        sys.exit(1)
    
    if not check_dependencies():
        sys.exit(1)
    
    # Build pytest command
    cmd = ["python", "-m", "pytest"]
    
    if args.verbose:
        cmd.append("-v")
    
    if args.coverage:
        cmd.extend(["--cov=cache_store", "--cov=server", "--cov-report=term-missing"])
    
    # Add test selection
    if args.unit:
        cmd.extend(["-m", "unit"])
    elif args.integration:
        cmd.extend(["-m", "integration"])
    elif args.fast:
        cmd.extend(["-m", "not slow"])
    
    if args.file:
        if args.test:
            cmd.append(f"{args.file}::{args.test}")
        else:
            cmd.append(args.file)
    elif args.test:
        cmd.extend(["-k", args.test])
    
    # Special handling for mock tests
    if args.mock:
        cmd.append("tests/test_grpc_service_mock.py")
    
    # If no specific tests selected, run appropriate default
    if not any([args.unit, args.integration, args.fast, args.file, args.test, args.mock]):
        # Run unit tests that work without protobuf issues
        print("No specific test type selected. Running unit tests...")
        cmd.extend(["tests/test_cache_entry.py", "tests/test_cache_store.py"])
    
    # Run the tests
    success = run_command(cmd, "Running tests")
    
    if success:
        print("\nAll tests completed successfully!")
        
        # Provide helpful next steps
        print("\nAvailable test commands:")
        print("   python run_tests.py --unit          # Run unit tests only")
        print("   python run_tests.py --integration   # Run integration tests")
        print("   python run_tests.py --mock          # Run mocked gRPC tests")
        print("   python run_tests.py --coverage      # Run with coverage report")
        print("   python run_tests.py --file tests/test_cache_store.py  # Run specific file")
        print("   python run_tests.py --test test_set_and_get     # Run specific test")
        
    else:
        print("\nTroubleshooting tips:")
        print("   1. Make sure virtual environment is activated: . ./venv/bin/activate")
        print("   2. Install dependencies: pip install -r requirements-dev.txt")
        print("   3. For full gRPC tests, generate protobuf: make gen")
        print("   4. Check TEST_README.md for detailed instructions")
        sys.exit(1)


if __name__ == "__main__":
    main()