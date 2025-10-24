# Troubleshooting

## Common Issues

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
