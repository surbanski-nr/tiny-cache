# Agent Instructions for Roo

This document provides context and instructions for AI agents working on the tiny-cache projects.

## Project Overview

A lightweight cache service that works as a sidecar, implementing a gRPC server with GET/SET/TTL/LRU functionality. The cache uses an in-memory dictionary for storage with LRU eviction policy when reaching maximum size and TTL (Time To Live) support for each cache entry.

## Environment Setup

Both projects use Python virtual environments. **IMPORTANT**: Before running any tests or Python commands, you must activate the virtual environment:

```bash
. ./venv/bin/activate
```

This is critical for:
- Running tests
- Installing dependencies
- Executing Python scripts
- Using project-specific tools

## Development Workflow

### For tiny-cache:
1. Activate virtual environment: `. ./venv/bin/activate`
2. Install dependencies: `pip install -r requirements.txt`
3. Generate protobuf files: `make gen`
4. Run server: `python server.py`
5. Alternative Docker setup: `docker-compose build && docker-compose up`

### General Guidelines:
- Always activate the venv before running any Python-related commands
- Check for requirements.txt files in project directories
- Look for Makefile targets for common operations
- Consider Docker alternatives when available

## Testing

When running tests for either project:
1. **MUST** activate virtual environment first: `. ./venv/bin/activate`
2. Then run the appropriate test commands
3. Common test runners: pytest, unittest, or custom test scripts

## Coding Standards

### Code Style Requirements
- **NO EMOTICONS**: Do not use any emoticons, emojis, or Unicode symbols in code, comments, documentation, or output messages
- Use clear, professional text instead of emoticons (e.g., use "SUCCESS" and "ERROR")
- Maintain clean, readable code without visual decorations
- Focus on technical accuracy and clarity over visual appeal

### Documentation Standards
- Write clear, concise documentation without emoticons
- Use standard markdown formatting
- Provide practical examples and usage instructions
- Keep documentation professional and technical

## Documentation Organization

### File Structure
- **README.md**: Main project documentation (MUST remain in root directory)
- **AGENTS.md**: AI agent instructions (MUST remain in root directory)
- **docs/**: All other documentation files (testing guides, architecture docs, etc.)

### Documentation Guidelines
- Only README.md and AGENTS.md should be in the root directory
- All other documentation files MUST be placed in the `docs/` directory
- This includes: testing documentation, architecture guides, API docs, tutorials, etc.
- When creating new documentation, always place it in `docs/` unless it's README.md or AGENTS.md
- Reference documentation in `docs/` from README.md when appropriate

### Reading Documentation
- Start with README.md for project overview and setup
- Check `docs/` directory for detailed documentation:
  - Testing guides and procedures
  - Architecture documentation
  - Technical specifications
  - Development guides

## Testing Requirements

### Mandatory Testing Protocol
- **ALL code changes REQUIRE corresponding tests**
- **ALL tests MUST pass before any change is considered complete**
- No exceptions to the testing requirement - every modification needs test coverage
- Use pytest framework for all Python testing

### Testing Workflow
1. Write or update tests for any code changes
2. Run full test suite: `python run_tests.py --coverage`
3. Ensure 90%+ code coverage is maintained
4. All tests must pass before considering work complete
5. Update test documentation if new test patterns are introduced

### Test Categories
- Unit tests: Test individual components in isolation
- Integration tests: Test component interactions
- Coverage tests: Ensure adequate code coverage
- Performance tests: Validate cache performance requirements

## Notes for AI Agents

- The virtual environment activation is not optional - it's required for proper dependency management
- Both projects may have specific build steps (like protobuf generation in tiny-cache)
- Check for project-specific documentation in README.md files
- Look for Makefile or setup scripts for automated workflows
- **CRITICAL**: Always follow the testing requirements - no code changes without tests and passing test suite
- **CRITICAL**: Never use emoticons or emojis in any project files or outputs
