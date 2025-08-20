# Troubleshooting

## Common Issues

### Authentication Problems

#### "Authentication failed" or "Invalid API key"

**Symptoms:**
- Commands return authentication errors
- `hcli whoami` fails

**Solutions:**
1. Verify your API key:
   ```bash
   hcli whoami
   ```

2. Re-authenticate:
   ```bash
   hcli logout
   hcli login
   ```

3. Check environment variables:
   ```bash
   echo $HCLI_API_KEY
   ```

#### "Unable to reach authentication server"

**Symptoms:**
- Login process hangs or times out
- Network connectivity errors

**Solutions:**
1. Check internet connection
2. Verify proxy settings if behind corporate firewall
3. Try alternative authentication method (API key vs OAuth)

