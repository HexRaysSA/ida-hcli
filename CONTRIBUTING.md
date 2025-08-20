# Contributing to ida-hcli

This document provides guidelines and information for contributors.

## Code of Conduct

This project adheres to a Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to support@hex-rays.com.

## How to Contribute

### Reporting Bugs

Before creating bug reports, please check the existing issues to avoid duplicates. When creating a bug report, include:

- A clear and descriptive title
- Steps to reproduce the issue
- Expected behavior vs. actual behavior
- Your environment (OS, Python version, ida-hcli version)
- Any relevant log output

### Suggesting Features

Feature requests are welcome! Please provide:

- A clear and descriptive title
- A detailed description of the proposed feature
- Use cases and motivation for the feature
- Any relevant examples or mockups

### Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/hexrays/ida-hcli.git
   cd ida-hcli
   ```

2. **Install dependencies:**
   ```bash
   uv sync
   ```

3. **Run the CLI in development mode:**
   ```bash
   uv run hcli --help
   ```

4. **Run tests:**
   ```bash
   uv run pytest
   ```

### Making Changes

1. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the coding standards below.

3. **Test your changes** thoroughly:
   ```bash
   # Run existing tests
   uv run pytest
   
   # Test CLI commands manually
   uv run hcli whoami
   uv run hcli plugin list
   ```

4. **Commit your changes** with a clear commit message:
   ```bash
   git commit -m "Add feature: description of your changes"
   ```

5. **Push to your fork and create a Pull Request**.

## Coding Standards

### Python Code Style

- Follow PEP 8 style guidelines
- Use type hints for function parameters and return values
- Use async/await for asynchronous operations
- Keep functions focused and small
- Use descriptive variable and function names

### Architecture Patterns

- **Commands**: Use `@async_command` decorator for async operations
- **Authentication**: Add `@require_auth` decorator for protected endpoints
- **Error Handling**: Use Rich console for user-friendly error messages
- **API Clients**: Extend patterns in `/src/hcli/lib/api/`
- **Configuration**: Add new environment variables to the `ENV` class

### Code Organization

- Keep related functionality in the same module
- Use the existing command structure for new commands
- Follow the established patterns for API integration
- Add appropriate error handling and user feedback

### Documentation

- Add docstrings to all public functions and classes
- Update README.md if adding new features
- Include examples in docstrings where helpful
- Keep inline comments concise and meaningful

## Testing

- Write tests for new functionality
- Ensure existing tests continue to pass
- Test both success and error cases
- Include integration tests for CLI commands where appropriate

## Pull Request Guidelines

1. **Title**: Use a clear, descriptive title
2. **Description**: Explain what changes you made and why
3. **Testing**: Describe how you tested your changes
4. **Breaking Changes**: Clearly mark any breaking changes
5. **Documentation**: Update documentation if needed

### Pull Request Checklist

- [ ] Code follows the project's coding standards
- [ ] Tests pass locally
- [ ] New functionality is tested
- [ ] Documentation is updated if needed
- [ ] Commit messages are clear and descriptive

## Development Tips

### Running Specific Commands

```bash
# Test authentication
uv run hcli whoami

# Test license operations
uv run hcli license list

# Test with debug output
HCLI_DEBUG=true uv run hcli license get 96-CB58-DC09-C5
```

### Environment Variables

Set these for development:

- `HCLI_DEBUG=true`: Enable debug output
- `HCLI_API_URL`: Override API endpoint for testing
- `HCLI_API_KEY`: Use API key authentication

### Common Issues

- **Authentication errors**: Make sure you're logged in with `hcli login`
- **Import errors**: Ensure all dependencies are installed with `uv sync`
- **Path issues**: Use absolute paths in tests and be mindful of cross-platform compatibility

## Getting Help

- Check existing issues and documentation first
- Ask questions in GitHub Discussions
- Contact support@hex-rays.com for sensitive issues

## License

By contributing to ida-hcli, you agree that your contributions will be licensed under the MIT License.