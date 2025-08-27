# Environment Variables

## IDA Pro Installation

### `HCLI_IDA_INSTALL_DIR`
Override the IDA Pro installation directory path.

**Type:** String  
**Default:** None (uses configuration file or auto-detection)  
**Example:** `export HCLI_IDA_INSTALL_DIR=/opt/ida-pro`

### `IDADIR`
Alternative environment variable for IDA Pro installation directory (fallback).

**Type:** String  
**Default:** None  
**Example:** `export IDADIR=/opt/ida-pro`

## Authentication Variables

### `HCLI_API_KEY`
Your API key for authentication.

**Type:** String  
**Default:** None  
**Example:** `export HCLI_API_KEY=hcli_1234567890abcdef`

## Debug and Logging

### `HCLI_DEBUG`
Enable debug mode with verbose logging.

**Type:** Boolean  
**Default:** `false`  
**Values:** `true`, `false`, `1`, `0`  
**Example:** `export HCLI_DEBUG=true`

## Network Configuration

### `HTTP_PROXY`
HTTP proxy server.

**Type:** String  
**Default:** None  
**Example:** `export HTTP_PROXY=http://proxy.company.com:8080`

### `HTTPS_PROXY`
HTTPS proxy server.

**Type:** String  
**Default:** None  
**Example:** `export HTTPS_PROXY=http://proxy.company.com:8080`

