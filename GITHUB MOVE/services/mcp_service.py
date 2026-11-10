import logging
import requests
from typing import Dict, Any, Tuple, Optional
from config import Config

logger = logging.getLogger("bea.mcp_service")

class MCPService:
    """
    Manages optional Model Context Protocol (MCP) integrations.
    MCP is completely optional and non-blocking: Gemini remains
    the primary standalone AI evaluation engine.
    """

    @classmethod
    def test_connection(cls, server_url: Optional[str] = None, auth_token: Optional[str] = None) -> Tuple[bool, Dict[str, Any], str]:
        """
        Tests connectivity to an external MCP server endpoint.
        Returns: (success, status_dict, message)
        """
        url = (server_url or Config.MCP_SERVER_URL).strip()
        token = (auth_token or Config.MCP_AUTH_TOKEN).strip()

        if not url:
            return False, {"status": "unconfigured"}, "MCP Server URL is not configured."

        headers = {"User-Agent": "BEA-AI-Assessment-System/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            # Ping the SSE / Health / Tools endpoint
            # Most MCP servers respond to /health, /tools, or root GET/OPTIONS
            test_url = url.rstrip("/")
            if not test_url.endswith("/health") and not test_url.endswith("/tools"):
                health_url = f"{test_url}/health"
            else:
                health_url = test_url

            try:
                response = requests.get(health_url, headers=headers, timeout=5)
            except Exception:
                # Fallback to root URL
                response = requests.get(test_url, headers=headers, timeout=5)

            if response.status_code in (200, 204):
                info = {
                    "status": "connected",
                    "http_status": response.status_code,
                    "endpoint": test_url
                }
                return True, info, f"MCP server reachable! (HTTP {response.status_code})"
            else:
                info = {
                    "status": "error",
                    "http_status": response.status_code,
                    "endpoint": test_url
                }
                return False, info, f"MCP server returned HTTP {response.status_code}: {response.text[:100]}"

        except Exception as e:
            logger.error(f"MCP connection test error: {e}")
            info = {"status": "unreachable", "error": str(e)}
            return False, info, f"Failed to connect to MCP server: {str(e)}"

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Returns the current configured MCP status."""
        return {
            "enabled": Config.MCP_ENABLED,
            "server_url": Config.MCP_SERVER_URL or "None",
            "has_auth_token": bool(Config.MCP_AUTH_TOKEN),
            "status": "enabled" if Config.MCP_ENABLED else "disabled"
        }
