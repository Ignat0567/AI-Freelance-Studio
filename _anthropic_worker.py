"""Isolated Anthropic/Claude API caller — runs in separate process.
Uses PowerShell for HTTPS since Python OpenSSL on Windows Store Python 3.13 is broken.
Reads payload from stdin. No hardcoded max_tokens cap — Claude generates full code."""
import sys
import os
import json
import subprocess
import base64
import tempfile

def main():
    if len(sys.argv) != 3:
        print("Usage: _anthropic_worker.py <url> <api_key>  (reads payload_json from stdin)", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    api_key = sys.argv[2]
    payload_json = sys.stdin.read().strip()

    payload_obj = json.loads(payload_json)

    safe_api_key = api_key.replace("'", "''")

    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("utf-8")

    for attempt in range(3):
        ps_code = f'''
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
try {{
    $body = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{payload_b64}"))
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = New-Object System.TimeSpan(0, 0, 180)
    $client.DefaultRequestHeaders.Add("x-api-key", "{safe_api_key}")
    $client.DefaultRequestHeaders.Add("anthropic-version", "2023-06-01")
    $content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, "application/json")
    $response = $client.PostAsync("{url}", $content).GetAwaiter().GetResult()
    $rawBytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
    $utf8 = [System.Text.Encoding]::UTF8.GetString($rawBytes)
    if (!$response.IsSuccessStatusCode) {{
        Write-Output $utf8
        exit 1
    }}
    Write-Output $utf8
}} catch {{
    Write-Error $_.Exception.Message
    exit 1
}}
'''
        fd, ps_path = tempfile.mkstemp(suffix=".ps1")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ps_code)

            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                capture_output=True, timeout=190,
            )

            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()

            if result.returncode != 0:
                err_text = (stderr + stdout).lower()
                if "timed out" in err_text or "срок" in err_text:
                    if attempt < 2:
                        continue
                    print("Timed out after 3 attempts", file=sys.stderr)
                    sys.exit(4)
                if "401" in err_text or "invalid" in err_text.lower():
                    print("Invalid Anthropic API key", file=sys.stderr)
                    sys.exit(2)
                if "500" in err_text and attempt < 2:
                    continue
                if attempt < 2:
                    continue
                print(f"PS error: {stderr[:300]}", file=sys.stderr)
                sys.exit(1)

            if not stdout:
                if attempt < 2:
                    continue
                print("Empty response", file=sys.stderr)
                sys.exit(1)

            sys.stdout.buffer.write(stdout.encode("utf-8"))
            sys.stdout.buffer.flush()
            return

        finally:
            try:
                os.unlink(ps_path)
            except OSError:
                pass

    print("Failed after 3 attempts", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
