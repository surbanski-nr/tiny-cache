# Testing Guide for tiny-cache

This document provides comprehensive information about testing the tiny-cache project.

## Overview

The tiny-cache project includes comprehensive test coverage for:
- **Unit Tests**: Individual components (CacheEntry, CacheStore)
- **Integration Tests**: Not currently implemented for the gRPC/HTTP layers

## Test Structure

```
├── tests/                       # Test directory
│   ├── __init__.py             # Python package initialization
│   ├── test_cache_entry.py     # Unit tests for CacheEntry class (12 tests)
│   └── test_cache_store.py     # Unit tests for CacheStore class (22 tests)
├── pytest.ini                  # Pytest configuration
├── requirements.txt             # Production dependencies
├── requirements-dev.txt         # Development/testing dependencies
├── run_tests.py                 # Test runner script
└── TEST_README.md              # This documentation
```

## Prerequisites

### 1. Virtual Environment Setup

**IMPORTANT**: Always activate the virtual environment before running tests:

```bash
# Activate virtual environment
. ./venv/bin/activate

# Install development dependencies (includes testing tools)
pip install -r requirements-dev.txt
```

**Note**: We use separate requirements files:
- `requirements.txt` - Production dependencies only (grpcio, protobuf, aiohttp)
- `requirements-dev.txt` - Development dependencies (includes pytest, coverage tools, linting)

### 2. Generate Protobuf Files (Optional)

For full integration tests, generate the protobuf files:

```bash
# Generate protobuf files
make gen
```

Note: the generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) are checked into this repo.

## Running Tests

### Run All Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=cache_store --cov=server
```

### Run Specific Test Categories

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only fast tests (exclude slow tests)
pytest -m "not slow"
```

### Run Specific Test Files

```bash
# Test CacheEntry class (12 tests)
pytest tests/test_cache_entry.py -v

# Test CacheStore class (22 tests)
pytest tests/test_cache_store.py -v

# Run both test files
python run_tests.py
```

### Run Specific Test Methods

```bash
# Run a specific test method
pytest test_cache_store.py::TestCacheStore::test_set_and_get_basic_functionality -v

# Run all tests in a specific class
pytest test_cache_store.py::TestCacheStore -v
```

## Test Categories and Markers

Tests are organized using pytest markers:

- `@pytest.mark.unit`: Fast unit tests for individual components
- `@pytest.mark.integration`: Integration tests that test component interactions
- `@pytest.mark.slow`: Tests that take longer to run (e.g., TTL expiration tests)

## Test Coverage

### CacheEntry Tests (`test_cache_entry.py`)

- Creation with different data types (string, int, bytes, list, dict)
- TTL handling (with/without TTL, zero TTL, negative TTL)
- Size calculation accuracy
- Creation time accuracy
- Edge cases (None values)

### CacheStore Tests (`test_cache_store.py`)

- Basic operations (get, set, delete)
- TTL expiration handling
- LRU eviction by item count
- Memory-based eviction
- Statistics tracking
- Thread safety
- Background cleanup thread
- Error handling
- Memory tracking accuracy
- Cache clearing

### gRPC Service Tests

Not currently implemented.

## Expected Test Results

When running tests, you should see output similar to:

```
================================ test session starts ================================
collected 45 items

test_cache_entry.py::TestCacheEntry::test_cache_entry_creation_with_string_value PASSED
test_cache_entry.py::TestCacheEntry::test_cache_entry_creation_with_ttl PASSED
...
test_cache_store.py::TestCacheStore::test_set_and_get_basic_functionality PASSED
test_cache_store.py::TestCacheStore::test_get_nonexistent_key PASSED

================================ 45 passed in 2.34s ================================
```

## Troubleshooting

### Common Issues

1. **Import Error: "pytest" could not be resolved**
   ```bash
   # Make sure virtual environment is activated and pytest is installed
   . ./venv/bin/activate
   pip install pytest pytest-asyncio pytest-mock
   ```

2. **Import Error: "cache_pb2" could not be resolved**
   ```bash
   # Generate protobuf files
   make gen
   ```

3. **Tests hanging or taking too long**
   ```bash
   # Run without slow tests
   pytest -m "not slow"
   # Or set shorter timeouts in test configuration
   ```

### Environment Variables

You can configure cache behavior during testing using environment variables:

```bash
# Set cache limits for testing
export CACHE_MAX_ITEMS=100
export CACHE_MAX_MEMORY_MB=10
export CACHE_CLEANUP_INTERVAL=1

# Run tests with custom configuration
pytest
```

## Writing New Tests

### Test Structure Template

```python
import pytest
from cache_store import CacheStore

class TestNewFeature:
    """Test cases for new feature."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.cache = CacheStore(max_items=10, max_memory_mb=1)
    
    def teardown_method(self):
        """Clean up after each test method."""
        if hasattr(self, 'cache'):
            self.cache.stop()
    
    @pytest.mark.unit
    def test_new_functionality(self):
        """Test new functionality."""
        # Arrange
        key = "test_key"
        value = "test_value"
        
        # Act
        result = self.cache.set(key, value)
        
        # Assert
        assert result is True
        assert self.cache.get(key) == value
```

### Best Practices

1. **Use descriptive test names** that explain what is being tested
2. **Follow AAA pattern**: Arrange, Act, Assert
3. **Clean up resources** in teardown methods
4. **Use appropriate markers** (@pytest.mark.unit, @pytest.mark.integration)
5. **Test edge cases** and error conditions
6. **Mock external dependencies** when appropriate

## Continuous Integration

For CI/CD pipelines, use:

```bash
# Install dependencies
. ./venv/bin/activate
pip install -r requirements.txt

# Generate protobuf (if needed)
make gen

# Run tests with coverage
pytest --cov=cache_store --cov=server --cov-report=xml

# Run only fast tests for quick feedback
pytest -m "not slow"
```

## Performance Testing

For performance testing, you can create additional test files:

```python
@pytest.mark.slow
def test_cache_performance():
    """Test cache performance with large datasets."""
    cache = CacheStore(max_items=10000, max_memory_mb=100)
    
    # Performance test implementation
    start_time = time.time()
    for i in range(1000):
        cache.set(f"key_{i}", f"value_{i}")
    end_time = time.time()
    
    assert end_time - start_time < 1.0  # Should complete in under 1 second
    cache.stop()
```

## Test Data Management

For tests requiring specific data:

```python
@pytest.fixture
def sample_cache_data():
    """Provide sample data for tests."""
    return {
        "key1": "value1",
        "key2": {"nested": "data"},
        "key3": [1, 2, 3, 4, 5]
    }

def test_with_sample_data(sample_cache_data):
    """Test using fixture data."""
    cache = CacheStore()
    for key, value in sample_cache_data.items():
        cache.set(key, value)
    # Test implementation
    cache.stop()
