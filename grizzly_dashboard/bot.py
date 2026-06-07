# -*- coding: utf-8 -*-
"""
GrizzlySMS, PVACodes, OTP Doctor Telegram Bot Control Center & AutoBuy Engine.
Runs on token: 6665214315:AAFtc3ucHQet-Q1656bz_qtlU-IigQ81ZJw
Restricted to Owner ID: 5145264491
"""

# ========== DNS FIX – Force IPv4 for api.telegram.org ==========
import socket
import asyncio

def force_ipv4_dns():
    original_getaddrinfo = socket.getaddrinfo
    def new_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, bytes):
            host = host.decode('utf-8')
        if host == 'api.telegram.org':
            # Force IPv4 family dynamically instead of hardcoding a single IP
            family = socket.AF_INET
        return original_getaddrinfo(host, port, family, type, proto, flags)
    socket.getaddrinfo = new_getaddrinfo
    print("Force IPv4 DNS for api.telegram.org applied.")

force_ipv4_dns()
# =================================================================

import os
import sys

# Apply DNS monkey patch first (optional – if dns_patch.py exists)
try:
    import dns_patch
except ImportError:
    # Fallback to local import path adjustments
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import dns_patch
    except ImportError:
        pass  # DNS already fixed above

import json
import time
import logging
import httpx

# Shared HTTP transport for connection pooling across all clients
_transport = httpx.AsyncHTTPTransport(
    retries=1,
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
)
_http_client = httpx.AsyncClient(
    transport=_transport,
    timeout=15
)

class SharedClient:
    def __init__(self, timeout=15):
        self.timeout = timeout

    async def __aenter__(self):
        return _http_client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("grizzly_bot")

# Configuration File Resolution
CONFIG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "grizzly_config.json"))
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "grizzly_config.json"))
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "grizzly_config.json"))

# Bot Credentials
BOT_TOKEN = "6665214315:AAFtc3ucHQet-Q1656bz_qtlU-IigQ81ZJw"
OWNER_ID = 5145264491

# Jio API Headers (shared)
JIO_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,ar;q=0.8",
    "connection": "keep-alive",
    "referer": "https://www.jio.com/selfcare/paybill/mobility/",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}

# Provider country mappings
PVACODES_COUNTRY_MAP = {
    "22": "India", "0": "Russia", "12": "USA", "1": "Ukraine", "7": "Kazakhstan",
    "9": "China", "15": "United Kingdom", "8": "Kyrgyzstan", "16": "Indonesia", "11": "Brazil"
}

OTPDOCTOR_COUNTRY_MAP = {
    "22": "in", "0": "ru", "12": "us", "1": "ua", "7": "kz",
    "9": "cn", "15": "uk", "8": "kg", "16": "id", "11": "br"
}

# Global Runtime State
is_autobuy_running = False
stop_autobuy = False
active_rentals = {}          # id -> rental_dict
sms_notified_ids = set()     # Set of alerted rental IDs
valid_numbers_found = []     # Dict list: {"phone", "provider", "time"}
collected_links = []         # Persistent list of collected links for batch of 10
history_since_last_collect = [] # Persistent list of all collected links since last manual collection
pvacodes_timestamps = []     # Rate limiter timestamps
last_pva_refresh = {}        # id -> timestamp
autobuy_tasks = []
bot_start_time = time.time()     # Track session start to skip old rentals
jio_otp_lock = None  # Will be set to asyncio.Lock() when event loop starts
processing_activation_ids = set() # Track active OTP validations/activations to avoid race conditions

# --- CONFIG MANAGEMENT ---
def load_grizzly_config() -> dict:
    global active_rentals, valid_numbers_found, sms_notified_ids, collected_links, history_since_last_collect
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                # Load persistent data if exists
                active_rentals = cfg.get("active_rentals", {})
                valid_numbers_found = cfg.get("valid_numbers_found", [])
                sms_notified_ids = set(cfg.get("sms_notified_ids", []))
                collected_links = cfg.get("collected_links", [])
                history_since_last_collect = cfg.get("history_since_last_collect", [])
                return cfg
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            
    # Default config template
    default_cfg = {
        "api_keys": {
            "partner": "",
            "bearerToken": "",
            "sessionToken": "",
            "pvacodesKey": "",
            "pvacodesCookie": "",
            "pvacodesEmail": "",
            "pvacodesPassword": "",
            "otpdoctorKey": ""
        },
        "autobuy_settings": {
            "selected_apis": ["partner"],
            "country_id": "22",
            "services": {
                "partner": "Jio5, Jio10, Jio11",
                "user": "Jio5, Jio10, Jio11",
                "pvacodes": "Jio5",
                "otpdoctor": "My jio.com - 🇮🇳5"
            },
            "max_prices": {
                "partner": 0.135,
                "user": 0.135,
                "pvacodes": 0.135,
                "otpdoctor": 20.0
            },
            "auto_poll_sms": True
        },
        "is_autobuy_running": False,
        "google_one_activation": True   # Enable automatic Google One activation
    }
    return default_cfg

def save_grizzly_config(cfg: dict):
    global active_rentals, valid_numbers_found, sms_notified_ids, collected_links, history_since_last_collect
    try:
        # Clean up active_rentals: remove cancelled/completed rentals that expired more than 2 hours ago
        now = time.time()
        active_rentals = {
            r_id: r for r_id, r in active_rentals.items()
            if not (r.get("status") in ("cancelled", "completed") and now - r.get("endTime", 0) > 7200)
        }
        # Clean up sms_notified_ids: keep only IDs of active rentals
        sms_notified_ids = {r_id for r_id in sms_notified_ids if r_id in active_rentals}

        # Save runtime lists to config
        cfg["active_rentals"] = active_rentals
        cfg["valid_numbers_found"] = valid_numbers_found
        cfg["sms_notified_ids"] = list(sms_notified_ids)
        cfg["collected_links"] = collected_links
        cfg["history_since_last_collect"] = history_since_last_collect
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

# --- RATE LIMITER FOR PVACODES ---
async def check_pvacodes_rate_limit():
    global pvacodes_timestamps
    now = time.time()
    pvacodes_timestamps = [t for t in pvacodes_timestamps if now - t < 60]
    if len(pvacodes_timestamps) >= 80:
        sleep_time = 60 - (now - pvacodes_timestamps[0])
        if sleep_time > 0:
            logger.info(f"PVACodes Rate limit. Sleeping for {sleep_time:.2f}s")
            await asyncio.sleep(sleep_time)
    pvacodes_timestamps.append(time.time())

# --- JIO VALIDATOR ---
async def check_jio_number(phone: str) -> bool:
    cleaned = "".join(filter(str.isdigit, phone))
    if cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    url = f"https://www.jio.com/api/jio-recharge-service/recharge/mobility/number/{cleaned}"
    try:
        async with SharedClient( timeout=7) as client:
            r = await client.get(url, headers=JIO_HEADERS)
            if r.status_code == 200:
                data = r.json()
                if "primaryService" in data and data["primaryService"].get("serviceId") == cleaned:
                    return True
    except Exception as e:
        logger.error(f"Jio check failed for {cleaned}: {e}")
    return False

def clean_google_one_link(raw_url: str) -> str:
    import urllib.parse
    if not raw_url:
        return raw_url
    try:
        decoded_url = urllib.parse.unquote(raw_url)
        if "accounts.google.com" in decoded_url:
            parsed = urllib.parse.urlparse(decoded_url)
            params = urllib.parse.parse_qs(parsed.query)
            continue_url = params.get("continue", [None])[0]
            if continue_url:
                decoded_url = continue_url
                
        decoded_url = urllib.parse.unquote(decoded_url)
        if "serviceactivation.google.com" in decoded_url:
            decoded_url = decoded_url.replace("serviceactivation.google.com", "one.google.com/activate-plan")
            
        if "?" in decoded_url:
            decoded_url = decoded_url.split("?", 1)[0]
            
        decoded_url = urllib.parse.unquote(decoded_url)
        final_url = f"{decoded_url}?pli=1&g1_landing_page=5"
        return final_url
    except Exception as e:
        logger.error(f"Error cleaning google link: {e}")
        return raw_url

async def handle_new_activation_link(app_bot, raw_link: str) -> str:
    global collected_links, history_since_last_collect
    cleaned = clean_google_one_link(raw_link)
    
    cfg = load_grizzly_config()
    
    collected_links.append(cleaned)
    history_since_last_collect.append(cleaned)
    save_grizzly_config(cfg)
    
    logger.info(f"Collected new link: {cleaned}. Total collected in current batch: {len(collected_links)}")
    
    if len(collected_links) >= 10:
        links_to_send = collected_links[:10]
        temp_dir = os.path.dirname(CONFIG_FILE)
        file_path = os.path.join(temp_dir, "google_one_links.txt")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for lnk in links_to_send:
                    f.write(lnk + "\n")
            
            logger.info("Sending batch of 10 Google One links to owner...")
            with open(file_path, "rb") as doc:
                await app_bot.send_document(
                    chat_id=OWNER_ID,
                    document=doc,
                    filename="google_one_links.txt",
                    caption=f"🎉 Here is your batch of 10 Google One Activation Links!"
                )
                
            collected_links = collected_links[10:]
            save_grizzly_config(cfg)
            
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.error(f"Error sending document of links: {e}")
            
    return cleaned

# --- NEW JIO LOGIN FUNCTIONS ---
jio_sessions = {}

def find_activation_url(data):
    if isinstance(data, str):
        if "serviceactivation.google.com" in data or "one.google.com/activate-plan" in data:
            return data
    elif isinstance(data, dict):
        for v in data.values():
            res = find_activation_url(v)
            if res:
                return res
    elif isinstance(data, list):
        for item in data:
            res = find_activation_url(item)
            if res:
                return res
    return None

async def jio_send_otp(phone: str) -> bool:
    cleaned = "".join(filter(str.isdigit, phone))
    if cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
        
    url = "https://www.jio.com/api/jio-login-service/login/sendOtp"
    headers = JIO_HEADERS.copy()
    headers["content-type"] = "application/json"
    headers["referer"] = "https://www.jio.com/selfcare/login/"
    headers.pop("cookie", None)  # Remove stale hardcoded cookies
    
    payload = {"mobileNumber": cleaned, "loginFlowType": "MOBILE", "alternateNumber": ""}
    
    for attempt in range(1, 4):  # Retry up to 3 times
        try:
            import uuid
            async with httpx.AsyncClient(timeout=15) as client:
                # 1. Fetch NSC cookies by loading the selfcare login page
                await client.get("https://www.jio.com/selfcare/login/", headers=headers)
                
                # 2. Generate and set client-side UUID session cookies
                session_id = str(uuid.uuid4())
                client.cookies.set("JioSessionID", session_id, domain="www.jio.com")
                client.cookies.set("ssjsid", session_id, domain="www.jio.com")
                
                # 3. POST to sendOtp
                resp = await client.post(url, json=payload, headers=headers)
                logger.info(f"Jio sendOtp response (attempt {attempt}): status={resp.status_code} body={resp.text[:500]}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("responseCode") == "200":
                        # Save cookies returned by the response + cookies set client-side
                        cookies_dict = {cookie.name: cookie.value for cookie in client.cookies.jar}
                        jio_sessions[cleaned] = cookies_dict
                        return True
                    else:
                        logger.warning(f"Jio sendOtp rejected (attempt {attempt}): {data}")
                else:
                    logger.warning(f"Jio sendOtp HTTP {resp.status_code} (attempt {attempt})")
        except Exception as e:
            logger.error(f"Jio send OTP error (attempt {attempt}): {e}")
        
        if attempt < 3:
            await asyncio.sleep(2)
    
    return False

async def jio_validate_otp(phone: str, otp: str) -> dict:
    cleaned = "".join(filter(str.isdigit, phone))
    if cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
        
    url = "https://www.jio.com/api/jio-login-service/login/validateOtp"
    headers = JIO_HEADERS.copy()
    headers["content-type"] = "application/json"
    headers["referer"] = "https://www.jio.com/selfcare/login/"
    headers.pop("cookie", None)  # Remove stale hardcoded cookies
    
    cookies_dict = jio_sessions.get(cleaned, {})
    payload = {"otp": otp, "mobileNumber": cleaned, "loginFlowType": "MOBILE"}
    try:
        async with httpx.AsyncClient(timeout=15, cookies=cookies_dict) as client:
            resp = await client.post(url, json=payload, headers=headers)
            logger.info(f"Jio validateOtp response: status={resp.status_code} body={resp.text[:500]}")
            if resp.status_code == 200:
                # Merge cookies
                merged_cookies = cookies_dict.copy()
                merged_cookies.update({cookie.name: cookie.value for cookie in client.cookies.jar})
                
                # Check for successful response
                data = resp.json()
                success = data.get("responseCode") == "200" or "token" in data or data.get("success") == True or "ssoToken" in data or "authToken" in data
                
                if not success:
                    err_reason = f"Jio validateOtp rejected: {data}"
                    logger.warning(err_reason)
                    return {"success": False, "error_reason": err_reason}
                jio_sessions[cleaned] = merged_cookies
                
                return {"success": success, "cookies": merged_cookies, "data": data}
            else:
                err_reason = f"Jio validateOtp HTTP {resp.status_code}"
                logger.warning(err_reason)
                return {"success": False, "error_reason": err_reason}
    except Exception as e:
        err_reason = f"Exception in jio_validate_otp: {e}"
        logger.error(err_reason)
        return {"success": False, "error_reason": err_reason}
    return {"success": False, "error_reason": "Unknown failure in jio_validate_otp"}

async def jio_google_one_flow(phone: str, session_cookies: dict) -> tuple[bool, str]:
    """Returns (True, Google One activation link) or (False, Error reason)."""
    import json
    import os
    import re
    
    cookies_str = json.dumps(session_cookies)
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        extractor_path = os.path.join(script_dir, "browser_extractor.js")
        
        logger.info(f"Launching browser_extractor.js for +{phone}...")
        proc = await asyncio.create_subprocess_exec(
            "node", extractor_path, cookies_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=script_dir
        )
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode('utf-8', errors='ignore')
        stderr_str = stderr.decode('utf-8', errors='ignore')
        
        logger.info(f"Browser extractor stdout: {stdout_str}")
        if stderr_str:
            logger.error(f"Browser extractor stderr: {stderr_str}")
            
        urls = re.findall(r'SUCCESS_URL:([^\s]+)', stdout_str)
        if urls:
            return True, urls[-1]
            
        submit_match = re.search(r'Submit response: (.+)', stdout_str)
        if submit_match:
            try:
                res_obj = json.loads(submit_match.group(1))
                if isinstance(res_obj, dict):
                    if "url" in res_obj:
                        return True, res_obj["url"]
                    elif "data" in res_obj and "url" in res_obj["data"]:
                        return True, res_obj["data"]["url"]
            except Exception:
                pass
                
        return False, f"Could not find activation URL in playwright output. Stdout: {stdout_str[:300]}"
    except Exception as e:
        logger.error(f"Error running browser_extractor.js: {e}")
        return False, f"Failed to run browser_extractor.js: {e}"


# --- OTP DOCTOR SERVICE FUZZY RESOLVER ---
async def resolve_otpdoctor_service(api_key: str, service_str: str, country_id: str) -> str:
    input_str = service_str.strip()
    if input_str.isdigit():
        return input_str
    
    country = OTPDOCTOR_COUNTRY_MAP.get(country_id, "in")
    url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getServices&api_key={api_key}&country={country}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return input_str
            services = resp.json()
            search = input_str.lower()
            
            # Exact match
            for s_id, s in services.items():
                if s.get("service_name", "").lower() == search:
                    return s_id
            
            # Sanitized match
            clean_search = "".join(filter(str.isalnum, search))
            for s_id, s in services.items():
                full_name = f"{s.get('service_name', '')} {s.get('server_name', '')}"
                clean_full = "".join(filter(str.isalnum, full_name.lower()))
                if clean_full == clean_search:
                    return s_id
            
            # Substring match
            for s_id, s in services.items():
                full_name = f"{s.get('service_name', '')} {s.get('server_name', '')}".lower()
                if search in full_name:
                    return s_id
    except Exception as e:
        logger.error(f"Error resolving OTP Doctor service name: {e}")
    return input_str

# --- PROVIDER BALANCE API ---
async def get_provider_balance(provider: str, keys: dict) -> float:
    try:
        if provider == "partner":
            api_key = keys.get("partner", "")
            if not api_key: return 0.0
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getBalance"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                txt = r.text.strip()
                if txt.startswith("GET_BALANCE:"):
                    return float(txt.split(":")[1])
        elif provider == "user":
            token = keys.get("bearerToken", "")
            session = keys.get("sessionToken", "")
            if not token or not session: return 0.0
            headers = {"Authorization": f"Bearer {token}", "x-session-token": session, "user-agent": "Mozilla/5.0"}
            url = "https://grizzlysms.com/api/sms-users/balance"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url, headers=headers)
                return float(r.json().get("balance", 0.0))
        elif provider == "pvacodes":
            api_key = keys.get("pvacodesKey", "")
            if not api_key: return 0.0
            await check_pvacodes_rate_limit()
            url = f"https://beta.pvacodes.com/app/api.php?do=check_balance&key={api_key}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                data = r.json()
                if data.get("status", {}).get("code") == "1000":
                    val = data.get("data")
                    if isinstance(val, dict):
                        return float(val.get("credits", 0.0))
                    return float(val or 0.0)
        elif provider == "otpdoctor":
            api_key = keys.get("otpdoctorKey", "")
            if not api_key: return 0.0
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getBalance&api_key={api_key}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                txt = r.text.strip()
                if txt.startswith("ACCESS_BALANCE:"):
                    return float(txt.split(":")[1])
    except Exception as e:
        logger.error(f"Error checking balance for {provider}: {e}")
    return 0.0

# --- PROVIDER RENT NUMBER API ---
async def rent_number(provider: str, country_id: str, service: str, max_price: float, keys: dict) -> dict:
    try:
        if provider == "partner":
            api_key = keys.get("partner", "")
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=getNumberV2&service={service}&country={country_id}&maxPrice={max_price}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                txt = r.text.strip()
                try:
                    data = r.json()
                    if "activationId" in data:
                        return {"ok": True, "id": str(data["activationId"]), "phone": str(data["phoneNumber"]), "cost": float(data.get("activationCost", 0.0))}
                except Exception:
                    pass
                if txt.startswith("ACCESS_NUMBER:"):
                    parts = txt.split(":")
                    return {"ok": True, "id": parts[1], "phone": parts[2], "cost": float(parts[3]) if len(parts) > 3 else max_price}
                elif "NO_BALANCE" in txt:
                    return {"ok": False, "error": "NO_BALANCE"}
                return {"ok": False, "error": txt}
                
        elif provider == "user":
            token = keys.get("bearerToken", "")
            session = keys.get("sessionToken", "")
            headers = {"Authorization": f"Bearer {token}", "x-session-token": session, "user-agent": "Mozilla/5.0"}
            url = f"https://grizzlysms.com/api/sms-users/get-number/{country_id}/{service}?maxPrice={max_price}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url, headers=headers)
                data = r.json()
                if "value" in data and str(data["value"]).isdigit():
                    return {"ok": True, "id": str(data["value"]), "phone": "Pending", "cost": max_price}
                return {"ok": False, "error": str(data)}
                
        elif provider == "pvacodes":
            api_key = keys.get("pvacodesKey", "")
            pva_country = PVACODES_COUNTRY_MAP.get(country_id, country_id)
            await check_pvacodes_rate_limit()
            url = f"https://beta.pvacodes.com/app/api.php?do=get_number&country={pva_country}&app={service}&key={api_key}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                data = r.json()
                if data.get("status", {}).get("code") == "1000":
                    phone_str = data.get("data", "")
                    if isinstance(phone_str, str):
                        phone_str = phone_str.replace("+", "")
                    else:
                        phone_str = str(phone_str)
                    return {"ok": True, "id": str(data.get("id")), "phone": phone_str, "cost": 0.0}
                elif data.get("status", {}).get("code") == "1003":
                    return {"ok": False, "error": "NO_BALANCE"}
                return {"ok": False, "error": data.get("status", {}).get("message", "Unknown error")}
                
        elif provider == "otpdoctor":
            api_key = keys.get("otpdoctorKey", "")
            otp_country = OTPDOCTOR_COUNTRY_MAP.get(country_id, "in")
            resolved_id = await resolve_otpdoctor_service(api_key, service, country_id)
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getNumber&api_key={api_key}&service={resolved_id}&country={otp_country}"
            if max_price:
                url += f"&maxPrice={max_price}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                txt = r.text.strip()
                if txt.startswith("ACCESS_NUMBER:"):
                    parts = txt.split(":")
                    return {"ok": True, "id": parts[1], "phone": parts[2], "cost": max_price}
                elif "NO_BALANCE" in txt:
                    return {"ok": False, "error": "NO_BALANCE"}
                return {"ok": False, "error": txt}
    except Exception as e:
        logger.error(f"Rent failed for {provider}: {e}")
        return {"ok": False, "error": str(e)}

# --- PROVIDER CANCEL API ---
async def cancel_number(provider: str, act_id: str, keys: dict, number: str = None) -> bool:
    try:
        if provider == "partner":
            api_key = keys.get("partner", "")
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=setStatus&status=8&id={act_id}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                return "ACCESS_CANCEL" in r.text or "ACCESS_READY" in r.text
        elif provider == "user":
            api_key = keys.get("partner", "")
            if api_key:
                url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=setStatus&status=8&id={act_id}"
                async with SharedClient( timeout=15) as client:
                    r = await client.get(url)
                    return "ACCESS_CANCEL" in r.text or "ACCESS_READY" in r.text
        elif provider == "pvacodes":
            api_key = keys.get("pvacodesKey", "")
            await check_pvacodes_rate_limit()
            url = f"https://beta.pvacodes.com/app/api.php?do=cancel_number&number_id={act_id}&key={api_key}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                return r.json().get("status", {}).get("code") == "1000"
        elif provider == "otpdoctor":
            api_key = keys.get("otpdoctorKey", "")
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=setStatus&api_key={api_key}&id={act_id}&status=8"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                return "ACCESS_CANCEL" in r.text or "ACCESS_READY" in r.text
    except Exception as e:
        logger.error(f"Cancel failed for {provider}: {e}")
    return False

# --- PROVIDER COMPLETE API ---
async def complete_number(provider: str, act_id: str, keys: dict) -> bool:
    try:
        if provider == "partner" or provider == "user":
            api_key = keys.get("partner", "")
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={api_key}&action=setStatus&status=3&id={act_id}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                return "ACCESS_ACTIVATION" in r.text or "ACCESS_READY" in r.text
        elif provider == "pvacodes":
            return True
        elif provider == "otpdoctor":
            api_key = keys.get("otpdoctorKey", "")
            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=setStatus&api_key={api_key}&id={act_id}&status=3"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                return "ACCESS_ACTIVATION" in r.text or "ACCESS_READY" in r.text
    except Exception as e:
        logger.error(f"Complete failed for {provider}: {e}")
    return False

# --- PVACODES AUTO LOGIN & COOKIE MANAGER ---
async def get_pvacodes_cookie(keys: dict, force_refresh: bool = False) -> str:
    email = keys.get("pvacodesEmail", "")
    password = keys.get("pvacodesPassword", "")
    if not email or not password:
        return keys.get("pvacodesCookie", "")
        
    current_cookie = keys.get("pvacodesCookie", "")
    
    if current_cookie and not force_refresh:
        # Validate current cookie
        try:
            url = "https://beta.pvacodes.com/app/index.php?page=user/number&search=&filter_type=temporary"
            headers = {
                "Cookie": current_cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                "Referer": "https://beta.pvacodes.com/app/index?page=user/app"
            }
            async with SharedClient( timeout=10) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200 and "Number history" in r.text and "Welcome Back" not in r.text:
                    return current_cookie
        except Exception as e:
            logger.debug(f"Error validating PVACodes cookie: {e}")
            
    logger.info("Logging in to PVACodes automatically...")
    login_url = "https://beta.pvacodes.com/app/index.php?page=user/login"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(login_url, headers=headers)
            import re
            csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
            if not csrf_match:
                csrf_match = re.search(r'meta\s+name="csrf-token"\s+content="([^"]+)"', r.text)
            if not csrf_match:
                logger.error("Could not find CSRF token on PVACodes login page")
                return ""
            
            csrf_token = csrf_match.group(1)
            payload = {
                "csrf_token": csrf_token,
                "login_email": email,
                "login_pass": password,
                "login_submit": ""
            }
            
            r2 = await client.post(
                login_url, 
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": login_url}, 
                data=payload
            )
            
            if r2.status_code == 200:
                try:
                    res_data = r2.json()
                    if res_data.get("status", {}).get("code") == "1000":
                        session_id = client.cookies.get("SECURE_SESSION_ID")
                        if session_id:
                            new_cookie = f"SECURE_SESSION_ID={session_id}"
                            # Save back to config
                            cfg = load_grizzly_config()
                            cfg["api_keys"]["pvacodesCookie"] = new_cookie
                            save_grizzly_config(cfg)
                            # Update keys dict
                            keys["pvacodesCookie"] = new_cookie
                            logger.info("Successfully logged in to PVACodes and saved session cookie!")
                            return new_cookie
                        else:
                            logger.error("SECURE_SESSION_ID cookie not found in login response cookies.")
                    else:
                        logger.error(f"PVACodes login JSON error: {res_data}")
                except Exception as je:
                    logger.error(f"Failed to parse PVACodes login response: {je}. Raw response: {r2.text[:200]}")
            else:
                logger.error(f"PVACodes login request failed with status: {r2.status_code}")
    except Exception as e:
        logger.error(f"Failed to auto-login to PVACodes: {e}")
        
    return ""

# --- PVACODES SMS REFRESH ---
async def refresh_pvacodes_sms(act_id: str, keys: dict):
    cookie = await get_pvacodes_cookie(keys)
    if not cookie:
        return
    
    url_app = "https://beta.pvacodes.com/app/index.php?page=user/app"
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Referer": "https://beta.pvacodes.com/app/index?page=user/app"
    }
    
    try:
        import re
        async with SharedClient( timeout=30) as client:
            # 1. Fetch user app page to extract CSRF token
            r_get = await client.get(url_app, headers=headers)
            if r_get.status_code == 200 and ("Login -" in r_get.text or "Welcome Back" in r_get.text):
                logger.info("PVACodes SMS refresh session expired. Re-authenticating...")
                new_cookie = await get_pvacodes_cookie(keys, force_refresh=True)
                if new_cookie:
                    headers["Cookie"] = new_cookie
                    r_get = await client.get(url_app, headers=headers)
            
            if r_get.status_code != 200:
                logger.error(f"Failed to load PVACodes app page, status code: {r_get.status_code}")
                return
            
            # Extract CSRF token
            csrf_token = ""
            csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_get.text)
            if not csrf_match:
                csrf_match = re.search(r'meta\s+name="csrf-token"\s+content="([^"]+)"', r_get.text)
            if csrf_match:
                csrf_token = csrf_match.group(1)
            else:
                logger.warning("Could not find CSRF token on PVACodes app page, trying without it...")
            
            # 2. POST to refresh SMS
            post_headers = headers.copy()
            post_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            post_headers["x-requested-with"] = "XMLHttpRequest"
            if csrf_token:
                post_headers["x-csrf-token"] = csrf_token
            
            payload = f"page=user%2Fapp&refresh_sms=1&id={act_id}"
            r_post = await client.post(url_app, headers=post_headers, content=payload)
            if r_post.status_code == 200:
                logger.info(f"PVACodes SMS refresh triggered for ID {act_id}. Response status code: {r_post.status_code}")
            else:
                logger.error(f"PVACodes SMS refresh POST failed with status {r_post.status_code}: {r_post.text[:200]}")
    except Exception as e:
        logger.error(f"PVACodes SMS refresh failed for {act_id}: {e}")

# --- ACTIVE RENTALS LOADER & MANAGER ---
async def refresh_active_rentals(keys: dict):
    global active_rentals
    new_rentals = {}
    
    # 1. Partner
    if keys.get("partner"):
        try:
            url = f"https://api.grizzlysms.com/stubs/handler_api.php?api_key={keys['partner']}&action=getActiveActivations"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for item in data:
                            r_id = str(item["activationId"])
                            new_rentals[r_id] = {
                                "id": r_id,
                                "phone": str(item["phoneNumber"]),
                                "cost": float(item.get("activationCost", 0.135)),
                                "service": item.get("serviceCode", "unknown"),
                                "country": item.get("countryName", "N/A"),
                                "status": item.get("activationStatus", "active"),
                                "code": item.get("smsCode") or "",
                                "provider": "Grizzly Partner",
                                "providerClass": "partner",
                                "endTime": active_rentals.get(r_id, {}).get("endTime", time.time() + 900)
                            }
        except Exception as e:
            logger.debug(f"Partner refresh error: {e}")

    # 2. User
    if keys.get("bearerToken") and keys.get("sessionToken"):
        try:
            headers = {"Authorization": f"Bearer {keys['bearerToken']}", "x-session-token": keys['sessionToken'], "user-agent": "Mozilla/5.0"}
            url = "https://grizzlysms.com/api/sms-users/numbers"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for item in data:
                            r_id = str(item["id"])
                            end_t = item.get("timestamp_end", time.time() + 900)
                            new_rentals[r_id] = {
                                "id": r_id,
                                "phone": str(item["number"]),
                                "cost": float(item.get("price", 0.135)),
                                "service": item.get("service", {}).get("external_id", "unknown") if isinstance(item.get("service"), dict) else "unknown",
                                "country": item.get("countryCode", "India (22)"),
                                "status": item.get("status", "active"),
                                "code": item.get("code") or "",
                                "provider": "Grizzly User",
                                "providerClass": "user",
                                "endTime": end_t
                            }
        except Exception as e:
            logger.debug(f"User refresh error: {e}")

    # 3. PVACodes
    pva_cookie = await get_pvacodes_cookie(keys)
    if pva_cookie:
        try:
            url = "https://beta.pvacodes.com/app/index.php?page=user/number&search=&filter_type=temporary"
            headers = {
                "Cookie": pva_cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                "Referer": "https://beta.pvacodes.com/app/index?page=user/app"
            }
            async with SharedClient( timeout=15) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    if "Login -" in r.text or "Welcome Back" in r.text:
                        logger.info("PVACodes history scraper session expired. Re-authenticating...")
                        pva_cookie = await get_pvacodes_cookie(keys, force_refresh=True)
                        if pva_cookie:
                            headers["Cookie"] = pva_cookie
                            r = await client.get(url, headers=headers)
                
                if r.status_code == 200 and "Number history" in r.text:
                    import re
                    tr_blocks = re.findall(r'<tr[^>]*>.*?</tr>', r.text, re.DOTALL)
                    for tr in tr_blocks:
                        ids = re.findall(r'<td class="number-id">(\d+)</td>', tr)
                        if len(ids) < 2:
                            continue
                        r_id = ids[1]
                        
                        phone_match = re.search(r'id="data_' + r_id + r'"[^>]*>([^<]+)</span>', tr)
                        phone = phone_match.group(1).strip().replace("+", "") if phone_match else ""
                        if not phone:
                            continue
                            
                        sms_match = re.search(r'id="data2_' + r_id + r'"[^>]*>([^<]*)</span>', tr)
                        sms_code = sms_match.group(1).strip() if sms_match else ""
                        
                        status_match = re.search(r'class="status-badge[^"]*">([^<]+)</span>', tr)
                        status = status_match.group(1).strip().lower() if status_match else "active"
                        
                        country_match = re.search(r'<td class="country">([^<]+)</td>', tr)
                        country = country_match.group(1).strip() if country_match else "India"
                        
                        app_match = re.search(r'<td class="app-name">([^<]+)</td>', tr)
                        app_name = app_match.group(1).strip() if app_match else "unknown"
                        
                        if r_id in active_rentals or "completed" not in status:
                            new_rentals[r_id] = {
                                "id": r_id,
                                "phone": phone,
                                "cost": 0.0,
                                "service": app_name,
                                "country": country,
                                "status": "active" if "completed" not in status and "cancel" not in status and "refund" not in status else status,
                                "code": sms_code,
                                "provider": "PVACodes",
                                "providerClass": "pvacodes",
                                "endTime": active_rentals.get(r_id, {}).get("endTime", time.time() + 900)
                            }
        except Exception as e:
            logger.error(f"PVACodes HTML history scrape error: {e}")
    elif keys.get("pvacodesKey"):
        try:
            await check_pvacodes_rate_limit()
            url = f"https://beta.pvacodes.com/app/api.php?do=get_history&key={keys['pvacodesKey']}"
            async with SharedClient( timeout=15) as client:
                r = await client.get(url)
                data = r.json()
                if data.get("status", {}).get("code") == "1000" and isinstance(data.get("data"), list):
                    for item in data["data"]:
                        r_id = str(item["number_id"])
                        new_rentals[r_id] = {
                            "id": r_id,
                            "phone": str(item["number"]).replace("+", ""),
                            "cost": 0.0,
                            "service": item.get("app", "unknown"),
                            "country": item.get("country", "India"),
                            "status": item.get("status", "active"),
                            "code": item.get("sms") or item.get("code") or "",
                            "provider": "PVACodes",
                            "providerClass": "pvacodes",
                            "endTime": active_rentals.get(r_id, {}).get("endTime", time.time() + 900)
                        }
        except Exception as e:
            logger.debug(f"PVACodes refresh error: {e}")

    # Merge OTP Doctor & locally kept active rentals
    for r_id, r in list(active_rentals.items()):
        if r.get("removedAt") and (time.time() - r["removedAt"] < 20):
            new_rentals[r_id] = r
        elif r.get("providerClass") == "otpdoctor" or (r.get("providerClass") == "pvacodes" and r_id not in new_rentals):
            if r.get("status") not in ("cancelled", "completed"):
                new_rentals[r_id] = r

    # Preserve custom/runtime fields and existing codes from active_rentals for items present in new_rentals
    for r_id, r in new_rentals.items():
        if r_id in active_rentals:
            existing = active_rentals[r_id]
            # Preserve code if we already have it but the new check doesn't have it
            if not r.get("code") and existing.get("code"):
                r["code"] = existing["code"]
            # Preserve original exact service code used for renting (e.g. Jio5, Jio11) to prevent generic display names overwriting them
            if existing.get("service") and existing["service"].lower() != "unknown":
                r["service"] = existing["service"]
            # Preserve phone number if existing is valid and new is pending/none
            if existing.get("phone") and existing["phone"].lower() not in ("none", "pending"):
                if not r.get("phone") or r["phone"].lower() in ("none", "pending"):
                    r["phone"] = existing["phone"]
            # Preserve other custom/runtime fields (e.g. otp_sent_time, otp_pending, google_one_link)
            for k, v in existing.items():
                if k not in r:
                    r[k] = v
                
    # Update global state
    active_rentals = new_rentals

# --- BACKGROUND GOOGLE ONE ACTIVATION FLOW ---
async def process_google_one_activation(app_bot, r_id: str, r: dict, keys: dict):
    if r_id in processing_activation_ids:
        logger.info(f"Activation already in progress for {r_id} in background task. Skipping duplicate.")
        return
    processing_activation_ids.add(r_id)
    try:
        phone = r.get("phone", "")
        code = r.get("code", "")
        prov = r.get("providerClass", "pvacodes")
        
        logger.info(f"🚀 Starting background Google One activation for +{phone} (ID: {r_id}) with code: {code}")
        
        # 1. Validate OTP
        validate_res = await jio_validate_otp(phone, code)
        if not validate_res.get("success"):
            err_reason = validate_res.get("error_reason", "Unknown")
            logger.error(f"❌ OTP validation failed for +{phone} (ID: {r_id}) in background. Reason: {err_reason}")
            await app_bot.send_message(OWNER_ID, f"❌ OTP validation failed for +{phone} (ID: {r_id}) in background.\n\n<b>Reason:</b> {err_reason}", parse_mode="HTML")
            await cancel_number(prov, r_id, keys)
            return
            
        # 2. Extract cookies and get Google One link
        cookie_dict = validate_res.get("cookies", {})
        success, link_or_err = await jio_google_one_flow(phone, cookie_dict)
        if success:
            cleaned_link = await handle_new_activation_link(app_bot, link_or_err)
            logger.info(f"🎉 Google One Activation Link generated for +{phone}: {cleaned_link}")
            await app_bot.send_message(
                OWNER_ID,
                f"🎉 <b>Google One Activation Link! (Background Processing)</b>\n\n"
                f"📱 Phone: <code>+{phone}</code>\n"
                f"🔗 Link: <a href=\"{cleaned_link}\">{cleaned_link}</a>\n"
                f"🆔 ID: <code>{r_id}</code>",
                parse_mode="HTML"
            )
            r["google_one_link"] = cleaned_link
            cfg = load_grizzly_config()
            save_grizzly_config(cfg)
        else:
            logger.error(f"❌ Failed to get Google One activation link for +{phone} (ID: {r_id}) in background. Reason: {link_or_err}")
            await app_bot.send_message(OWNER_ID, f"❌ Failed to get Google One activation link for +{phone} (ID: {r_id}).\n\n<b>Reason:</b> {link_or_err}", parse_mode="HTML")
            
        # 3. Cancel the number after completion
        await cancel_number(prov, r_id, keys)
        r["status"] = "cancelled"
        r["removedAt"] = time.time()
        cfg = load_grizzly_config()
        save_grizzly_config(cfg)
    except Exception as e:
        logger.error(f"Error in background Google One activation for ID {r_id}: {e}")
    finally:
        processing_activation_ids.discard(r_id)

# --- 24/7 BACKGROUND POLLER FOR SMS RECEIVED ---
async def grizzly_poller_task(app_bot):
    global active_rentals, sms_notified_ids, last_pva_refresh
    last_sms_api_check = {}   # id -> last get_sms timestamp
    pva_burst_done = set()    # IDs that finished their initial 4 burst attempts
    logger.info("🦁 SMS Poller Task Started")
    
    while True:
        try:
            cfg = load_grizzly_config()
            keys = cfg.get("api_keys", {})
            
            now_t = time.time()
            pending = [
                r for r in active_rentals.values()
                if not r.get("code")
                and r["id"] not in sms_notified_ids
                and r.get("endTime", 0) > now_t
                and (
                    r.get("status") not in ("cancelled", "completed")
                    or r.get("jio_valid")
                    or r.get("otp_pending")
                )
            ]
            
            if pending:
                # PVACodes cookie refresh
                pva_pending = [r for r in pending if r["providerClass"] == "pvacodes"]
                if pva_pending:
                    now = time.time()
                    for r in pva_pending:
                        # Only refresh & check SMS for valid numbers with OTP sent THIS session
                        if not r.get("otp_pending") and not r.get("jio_valid"):
                            continue
                        if r.get("otp_sent_time", 0) < bot_start_time:
                            continue  # Old number from previous session
                        
                        last_ref = last_pva_refresh.get(r["id"], 0)
                        if now - last_ref > 10:
                            last_pva_refresh[r["id"]] = now
                            await refresh_pvacodes_sms(r["id"], keys)
                            await asyncio.sleep(1)
                        
                        pva_key = keys.get("pvacodesKey")
                        phone = r.get("phone", "")
                        if pva_key and phone and phone.lower() not in ("none", "pending"):
                            try:
                                country_val = r.get("country", "India")
                                country_name = PVACODES_COUNTRY_MAP.get(country_val, country_val)
                                app = r.get("service", "Jio11")
                                if not phone.startswith("+"):
                                    formatted_phone = f"+{phone}"
                                else:
                                    formatted_phone = phone
                                
                                url = f"https://beta.pvacodes.com/app/api.php?do=get_sms&country={country_name}&app={app}&number={formatted_phone}&key={pva_key}"
                                
                                async def _do_get_sms_check(label=""):
                                    """Single get_sms API call. Returns True if code found. No rate limit — priority."""
                                    logger.info(f"Checking PVACodes SMS{label} at: {url}")
                                    async with SharedClient( timeout=10) as client:
                                        resp = await client.get(url)
                                        if resp.status_code == 200:
                                            res_json = resp.json()
                                            logger.info(f"PVACodes SMS response for {formatted_phone}{label}: {res_json}")
                                            if res_json.get("status", {}).get("code") == "1000":
                                                sms_code = str(res_json.get("data", ""))
                                                if sms_code:
                                                    r["code"] = sms_code
                                                    sms_notified_ids.add(r["id"])
                                                    save_grizzly_config(cfg)
                                                    logger.info(f"PVACodes API SMS: {r['phone']} -> {sms_code}")
                                                    await app_bot.send_message(
                                                        OWNER_ID,
                                                        f"💬 <b>SMS Received (PVACodes API)!</b>\n\n"
                                                        f"📱 Phone: <code>+{r['phone']}</code>\n"
                                                        f"🔑 Code: <code>{sms_code}</code>\n"
                                                        f"🆔 ID: <code>{r['id']}</code>",
                                                        parse_mode="HTML"
                                                    )
                                                    if r.get("jio_valid") and not r.get("google_one_link") and not r.get("activation_processing"):
                                                        r["activation_processing"] = True
                                                        asyncio.create_task(process_google_one_activation(app_bot, r["id"], r, keys))
                                                    return True
                                    return False
                                
                                r_id = r["id"]
                                if r_id not in pva_burst_done:
                                    # --- Burst mode: 4 quick attempts, 2s apart ---
                                    found = False
                                    for attempt in range(1, 5):
                                        found = await _do_get_sms_check(f" (burst {attempt}/4)")
                                        if found:
                                            break
                                        if attempt < 4:
                                            await asyncio.sleep(2)
                                    pva_burst_done.add(r_id)
                                    last_sms_api_check[r_id] = time.time()
                                else:
                                    # --- Periodic mode: 1 check every 60s ---
                                    last_check = last_sms_api_check.get(r_id, 0)
                                    if time.time() - last_check >= 60:
                                        last_sms_api_check[r_id] = time.time()
                                        await _do_get_sms_check(" (periodic 60s)")
                            except Exception as api_err:
                                logger.error(f"PVACodes get_sms API error: {api_err}")
                
                # OTP Doctor status check
                otp_pending = [r for r in pending if r["providerClass"] == "otpdoctor"]
                if otp_pending and keys.get("otpdoctorKey"):
                    for r in otp_pending:
                        if r["id"] in sms_notified_ids: continue
                        try:
                            url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getStatus&api_key={keys['otpdoctorKey']}&id={r['id']}"
                            async with SharedClient( timeout=10) as client:
                                resp = await client.get(url)
                                txt = resp.text.strip()
                                if txt.startswith("STATUS_OK:"):
                                    code = txt.split(":")[1]
                                    r["code"] = code
                                    sms_notified_ids.add(r["id"])
                                    save_grizzly_config(cfg)
                                    logger.info(f"OTP Doctor SMS: {r['phone']} -> {code}")
                                    await app_bot.send_message(
                                        OWNER_ID,
                                        f"💬 <b>SMS Received (OTP Doctor)!</b>\n\n"
                                        f"📱 Phone: <code>+{r['phone']}</code>\n"
                                        f"🔑 Code: <code>{code}</code>\n"
                                        f"🆔 ID: <code>{r['id']}</code>",
                                        parse_mode="HTML"
                                    )
                                    # Trigger background activation if Jio valid and not activated yet
                                    if r.get("jio_valid") and not r.get("google_one_link") and not r.get("activation_processing"):
                                        r["activation_processing"] = True
                                        asyncio.create_task(process_google_one_activation(app_bot, r["id"], r, keys))
                                elif "STATUS_CANCEL" in txt:
                                    r["status"] = "cancelled"
                                    r["removedAt"] = time.time()
                                    save_grizzly_config(cfg)
                        except Exception as e:
                            logger.error(f"Error checking OTP Doctor: {e}")
            
            # Refresh Grizzly/PVACodes active lists
            await refresh_active_rentals(keys)
            
            # Check for new codes received in refresh
            for r_id, r in list(active_rentals.items()):
                if r.get("code") and r_id not in sms_notified_ids:
                    sms_notified_ids.add(r_id)
                    save_grizzly_config(cfg)
                    logger.info(f"SMS Received ({r['provider']}): {r['phone']} -> {r['code']}")
                    await app_bot.send_message(
                        OWNER_ID,
                        f"💬 <b>SMS Received ({r['provider']})!</b>\n\n"
                        f"📱 Phone: <code>+{r['phone']}</code>\n"
                        f"🔑 Code: <code>{r['code']}</code>\n"
                        f"🆔 ID: <code>{r_id}</code>",
                        parse_mode="HTML"
                    )
                # In addition to notification, if we have a code and it is Jio valid, but has no link and is not processing:
                if (r.get("code") and r.get("jio_valid") and not r.get("google_one_link") 
                        and not r.get("activation_processing")
                        and r.get("status") not in ("cancelled", "completed")
                        and r.get("otp_sent_time", 0) >= bot_start_time):
                    r["activation_processing"] = True
                    asyncio.create_task(process_google_one_activation(app_bot, r_id, r, keys))
        except Exception as e:
            logger.error(f"Grizzly Poller loop error: {e}")
            
        await asyncio.sleep(10)

# --- AUTOBUY LOOP FOR A PROVIDER/SERVICE ---
async def run_autobuy_loop_for_provider(app_bot, provider: str, service: str, country: str, max_price: float):
    global is_autobuy_running, stop_autobuy, active_rentals, valid_numbers_found
    logger.info(f"🤖 AutoBuy Loop: {provider} | service={service} | maxPrice={max_price}")
    MIN_INTERVAL = 0.8
    
    cfg = load_grizzly_config()
    keys = cfg.get("api_keys", {})
    balance = await get_provider_balance(provider, keys)
    
    while is_autobuy_running and not stop_autobuy:
        start_t = time.time()
        
        if balance <= 0 or balance < max_price:
            logger.warning(f"[{provider}] Low balance ({balance}). Waiting 30s...")
            for _ in range(30):
                if stop_autobuy or not is_autobuy_running: break
                await asyncio.sleep(1)
            keys = load_grizzly_config().get("api_keys", {})
            balance = await get_provider_balance(provider, keys)
            continue
            
        order = await rent_number(provider, country, service, max_price, keys)
        if order.get("ok"):
            phone = order["phone"]
            act_id = order["id"]
            cost = order["cost"]
            
            prov_class = provider
            prov_name = "Grizzly Partner" if provider == "partner" else ("Grizzly User" if provider == "user" else provider.upper())
            
            # Register in global dictionary
            active_rentals[act_id] = {
                "id": act_id,
                "phone": phone,
                "cost": cost,
                "service": service,
                "country": country,
                "status": "active",
                "code": "",
                "provider": prov_name,
                "providerClass": prov_class,
                "endTime": time.time() + 900
            }
            save_grizzly_config(cfg)
            logger.info(f"[{provider}] Ordered +{phone} | ID: {act_id}")
            
            # Fire Jio validation task (modified to handle Google One)
            async def validate_jio_and_manage(ph=phone, aid=act_id, prov=provider, cst=cost):
                nonlocal keys
                # Auto-cancel if price too high
                if prov != "pvacodes" and prov != "otpdoctor" and cst is not None and cst > max_price:
                    await cancel_number(prov, aid, keys)
                    return
                
                # If phone is None/pending/empty, wait for history poller to update it
                current_phone = ph
                if not current_phone or current_phone.lower() in ("none", "pending"):
                    logger.info(f"[{prov}] Phone for ID {aid} is '{current_phone}'. Waiting up to 20s for history update...")
                    for _ in range(20):
                        await asyncio.sleep(1)
                        r_state = active_rentals.get(aid)
                        if r_state:
                            updated_phone = r_state.get("phone", "")
                            if updated_phone and updated_phone.lower() not in ("none", "pending"):
                                current_phone = updated_phone
                                logger.info(f"[{prov}] Phone for ID {aid} updated via history: +{current_phone}")
                                break
                                
                if not current_phone or current_phone.lower() in ("none", "pending"):
                    logger.warning(f"[{prov}] Phone for ID {aid} remained '{current_phone}' after 20s. Cancelling...")
                    await cancel_number(prov, aid, keys, number=current_phone)
                    if aid in active_rentals:
                        active_rentals[aid]["status"] = "cancelled"
                        active_rentals[aid]["removedAt"] = time.time()
                    return
                
                is_valid = await check_jio_number(current_phone)
                cfg_current = load_grizzly_config()
                if is_valid:
                    logger.info(f"✅ Jio Valid Number: +{current_phone}")
                    if aid in active_rentals:
                        active_rentals[aid]["jio_valid"] = True
                    
                    valid_numbers_found.append({
                        "phone": current_phone,
                        "provider": prov_name,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                    save_grizzly_config(cfg_current)
                    
                    await app_bot.send_message(
                        OWNER_ID,
                        f"✅ <b>Jio Valid Number Found!</b>\n\n"
                        f"📱 Phone: <code>+{current_phone}</code>\n"
                        f"🤖 Provider: <b>{prov_name}</b>\n"
                        f"🆔 ID: <code>{aid}</code>",
                        parse_mode="HTML"
                    )
                    
                    # --- START GOOGLE ONE ACTIVATION FLOW ---
                    if cfg_current.get("google_one_activation", True):
                        # 1. Send OTP
                        otp_sent = await jio_send_otp(current_phone)
                        if not otp_sent:
                            await app_bot.send_message(OWNER_ID, "❌ Failed to send OTP to Jio number.")
                            await asyncio.sleep(5)
                            await cancel_number(prov, aid, keys)
                            return

                        # Store OTP sent time
                        if aid in active_rentals:
                            active_rentals[aid]["otp_sent_time"] = time.time()
                            active_rentals[aid]["otp_pending"] = True
                            save_grizzly_config(cfg_current)

                        await app_bot.send_message(OWNER_ID, f"📤 OTP sent to +{current_phone}. Waiting for SMS...")

                        # 2. Wait for OTP code — check 5 times, once every 7 seconds
                        otp_code = None
                        
                        # Build get_sms URL for direct checking
                        pva_key = keys.get("pvacodesKey", "")
                        formatted_ph = f"+{current_phone}" if not current_phone.startswith("+") else current_phone
                        r_state = active_rentals.get(aid, {})
                        country_val = r_state.get("country", "India")
                        country_name = PVACODES_COUNTRY_MAP.get(country_val, country_val)
                        sms_app = r_state.get("service", "Jio11")
                        sms_url = f"https://beta.pvacodes.com/app/api.php?do=get_sms&country={country_name}&app={sms_app}&number={formatted_ph}&key={pva_key}"
                        
                        for attempt in range(1, 6):
                            logger.info(f"SMS poll attempt {attempt}/5 for {prov} ID {aid} (+{current_phone})")
                            
                            # Check if background poller already started activation FIRST
                            r = active_rentals.get(aid)
                            if r and r.get("activation_processing"):
                                logger.info(f"Background poller already processing activation for {aid}. Stopping wait.")
                                return
                            
                            # Check if code arrived (via poller or direct check)
                            if r and r.get("code"):
                                otp_code = r["code"]
                                logger.info(f"Code found for {aid}: {otp_code}")
                                break
                            
                            # Query SMS status based on provider class
                            if pva_key and prov == "pvacodes":
                                try:
                                    # Refresh PVACodes first
                                    await refresh_pvacodes_sms(aid, keys)
                                    await asyncio.sleep(1)
                                    
                                    # Direct get_sms — no rate limit (this is a priority call)
                                    logger.info(f"Direct SMS check for {aid}: {sms_url}")
                                    async with SharedClient( timeout=10) as client:
                                        resp = await client.get(sms_url)
                                        if resp.status_code == 200:
                                            res_json = resp.json()
                                            logger.info(f"Direct SMS response for {formatted_ph}: {res_json}")
                                            if res_json.get("status", {}).get("code") == "1000":
                                                code_val = str(res_json.get("data", ""))
                                                if code_val:
                                                    otp_code = code_val
                                                    if aid in active_rentals:
                                                        active_rentals[aid]["code"] = code_val
                                                    logger.info(f"✅ Direct SMS found for {aid}: {code_val}")
                                                    break
                                except Exception as sms_err:
                                    logger.error(f"Direct SMS check error for {aid}: {sms_err}")
                                    
                            elif prov == "otpdoctor" and keys.get("otpdoctorKey"):
                                try:
                                    url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getStatus&api_key={keys['otpdoctorKey']}&id={aid}"
                                    async with SharedClient( timeout=10) as client:
                                        resp = await client.get(url)
                                        txt = resp.text.strip()
                                        if txt.startswith("STATUS_OK:"):
                                            code_val = txt.split(":")[1]
                                            otp_code = code_val
                                            if aid in active_rentals:
                                                active_rentals[aid]["code"] = code_val
                                            break
                                except Exception as e:
                                    logger.error(f"OTP Doctor SMS check error for {aid}: {e}")
                                    
                            elif prov in ("partner", "user"):
                                try:
                                    await refresh_active_rentals(keys)
                                    r_state = active_rentals.get(aid)
                                    if r_state and r_state.get("code"):
                                        otp_code = r_state["code"]
                                        break
                                except Exception as e:
                                    logger.error(f"Grizzly SMS check error for {aid}: {e}")
                                    
                            if attempt < 5:
                                await asyncio.sleep(7)
                                
                        if not otp_code:
                            logger.info(f"⏱ No OTP received in 5 attempts (35s). Attempting cancellation for +{current_phone}...")
                            await app_bot.send_message(OWNER_ID, f"⏱ No OTP received within 5 checks (35s). Cancelling +{current_phone}...")
                            
                            cancelled = await cancel_number(prov, aid, keys, number=current_phone)
                            if not cancelled:
                                logger.warning(f"❌ Cancel failed for +{current_phone}. Retrying once...")
                                await app_bot.send_message(OWNER_ID, f"⚠️ Cancellation failed for +{current_phone}. Retrying SMS check & cancel once...")
                                await asyncio.sleep(7)
                                
                                # SMS check one last time (attempt 6)
                                if prov == "pvacodes" and keys.get("pvacodesKey"):
                                    try:
                                        await refresh_pvacodes_sms(aid, keys)
                                        async with SharedClient( timeout=10) as client:
                                            resp = await client.get(sms_url)
                                            if resp.status_code == 200:
                                                res_json = resp.json()
                                                if res_json.get("status", {}).get("code") == "1000":
                                                    code_val = str(res_json.get("data", ""))
                                                    if code_val:
                                                        otp_code = code_val
                                                        if aid in active_rentals:
                                                            active_rentals[aid]["code"] = code_val
                                    except Exception as sms_err:
                                        logger.error(f"PVACodes retry SMS check error: {sms_err}")
                                elif prov == "otpdoctor" and keys.get("otpdoctorKey"):
                                    try:
                                        url = f"https://www.otpdoctor.in/stubs/handler_api.php?action=getStatus&api_key={keys['otpdoctorKey']}&id={aid}"
                                        async with SharedClient( timeout=10) as client:
                                            resp = await client.get(url)
                                            txt = resp.text.strip()
                                            if txt.startswith("STATUS_OK:"):
                                                otp_code = txt.split(":")[1]
                                                if aid in active_rentals:
                                                    active_rentals[aid]["code"] = otp_code
                                    except Exception as e:
                                        logger.error(f"OTP Doctor retry SMS check error: {e}")
                                elif prov in ("partner", "user"):
                                    try:
                                        await refresh_active_rentals(keys)
                                        r_state = active_rentals.get(aid)
                                        if r_state and r_state.get("code"):
                                            otp_code = r_state["code"]
                                    except Exception as e:
                                        logger.error(f"Grizzly retry SMS check error: {e}")
                                        
                                if otp_code:
                                    logger.info(f"✅ OTP found on retry check for {aid}: {otp_code}")
                                else:
                                    logger.info(f"Re-attempting cancel for {aid}...")
                                    cancelled_again = await cancel_number(prov, aid, keys, number=current_phone)
                                    if cancelled_again:
                                        logger.info(f"✅ Cancel succeeded on second attempt for {aid}")
                                    else:
                                        logger.error(f"❌ Cancel failed again on second attempt for {aid}")
                                        
                            if not otp_code:
                                if aid in active_rentals:
                                    active_rentals[aid]["status"] = "cancelled"
                                    active_rentals[aid]["removedAt"] = time.time()
                                save_grizzly_config(cfg_current)
                                return
                        
                        # Double-check: if poller already started activation while we were finding the code
                        r_check = active_rentals.get(aid)
                        if (r_check and r_check.get("activation_processing")) or aid in processing_activation_ids:
                            logger.info(f"Activation already in progress/processing for {aid}. Skipping duplicate.")
                            return

                        # 3. Validate OTP (mark as processing to prevent background poller from double-processing)
                        processing_activation_ids.add(aid)
                        if aid in active_rentals:
                            active_rentals[aid]["activation_processing"] = True
                        
                        try:
                            validate_res = await jio_validate_otp(current_phone, otp_code)
                            if not validate_res.get("success"):
                                err_reason = validate_res.get("error_reason", "Unknown")
                                await app_bot.send_message(OWNER_ID, f"❌ OTP validation failed for +{current_phone}.\n\n<b>Reason:</b> {err_reason}", parse_mode="HTML")
                                await cancel_number(prov, aid, keys)
                                return

                            # 4. Extract cookies and get Google One link
                            cookie_dict = validate_res.get("cookies", {})
                            success, link_or_err = await jio_google_one_flow(current_phone, cookie_dict)
                            if success:
                                cleaned_link = await handle_new_activation_link(app_bot, link_or_err)
                                await app_bot.send_message(
                                    OWNER_ID,
                                    f"🎉 <b>Google One Activation Link!</b>\n\n"
                                    f"📱 Phone: <code>+{current_phone}</code>\n"
                                    f"🔗 Link: <a href=\"{cleaned_link}\">{cleaned_link}</a>\n"
                                    f"🆔 ID: <code>{aid}</code>",
                                    parse_mode="HTML"
                                )
                                if aid in active_rentals:
                                    active_rentals[aid]["google_one_link"] = cleaned_link
                                    save_grizzly_config(cfg_current)
                            else:
                                await app_bot.send_message(OWNER_ID, f"❌ Failed to get Google One activation link for +{current_phone}.\n\n<b>Reason:</b> {link_or_err}", parse_mode="HTML")

                            # 5. Cancel the number after completion
                            await cancel_number(prov, aid, keys)
                            if aid in active_rentals:
                                active_rentals[aid]["status"] = "cancelled"
                                active_rentals[aid]["removedAt"] = time.time()
                            save_grizzly_config(cfg_current)
                        finally:
                            processing_activation_ids.discard(aid)
                    else:
                        # Google One disabled, just keep number
                        await app_bot.send_message(OWNER_ID, f"✅ Valid Jio number +{current_phone} kept for further actions.")
                else:
                    # Cancel invalid number
                    wait_s = 5 if (prov == "pvacodes" or prov == "otpdoctor") else 120
                    logger.info(f"❌ Jio Invalid: +{current_phone} -> cancelling in {wait_s}s")
                    await asyncio.sleep(wait_s)
                    await cancel_number(prov, aid, keys, number=current_phone)
                    if aid in active_rentals:
                        active_rentals[aid]["status"] = "cancelled"
                        active_rentals[aid]["removedAt"] = time.time()
                    save_grizzly_config(cfg_current)
            
            asyncio.create_task(validate_jio_and_manage())
            
        elif order.get("error") == "NO_BALANCE":
            balance = 0.0
            continue
        else:
            logger.debug(f"[{provider}] Rent returned error: {order.get('error')}")
            
        elapsed = time.time() - start_t
        rem = MIN_INTERVAL - elapsed
        if rem > 0:
            await asyncio.sleep(rem)
            
    logger.info(f"AutoBuy Loop for {provider} stopped.")

async def start_autobuy_system(app_bot):
    global is_autobuy_running, stop_autobuy, autobuy_tasks
    if is_autobuy_running:
        return False
        
    cfg = load_grizzly_config()
    settings = cfg.get("autobuy_settings", {})
    selected_apis = settings.get("selected_apis", [])
    country_id = settings.get("country_id", "22")
    
    if not selected_apis:
        logger.warning("No APIs selected for AutoBuy.")
        return False
        
    is_autobuy_running = True
    stop_autobuy = False
    autobuy_tasks = []
    
    for prov in selected_apis:
        service_str = settings.get("services", {}).get(prov, "")
        max_p = float(settings.get("max_prices", {}).get(prov, 0.135))
        
        services = [s.strip() for s in service_str.split(",") if s.strip()]
        for svc in services:
            task = asyncio.create_task(
                run_autobuy_loop_for_provider(app_bot, prov, svc, country_id, max_p)
            )
            autobuy_tasks.append(task)
            
    cfg["is_autobuy_running"] = True
    save_grizzly_config(cfg)
    return True

async def stop_autobuy_system():
    global is_autobuy_running, stop_autobuy, autobuy_tasks
    if not is_autobuy_running:
        return False
        
    stop_autobuy = True
    is_autobuy_running = False
    
    if autobuy_tasks:
        await asyncio.gather(*autobuy_tasks, return_exceptions=True)
    autobuy_tasks = []
    
    cfg = load_grizzly_config()
    cfg["is_autobuy_running"] = False
    save_grizzly_config(cfg)
    return True

# --- KEYBOARD BUILDERS ---
def build_main_keyboard() -> InlineKeyboardMarkup:
    global is_autobuy_running
    ab_status = "🟢 Running" if is_autobuy_running else "🔴 Stopped"
    buttons = [
        [
            InlineKeyboardButton("💰 Check Balances", callback_data="grizzly_check_bal"),
            InlineKeyboardButton("🔄 Refresh", callback_data="grizzly_refresh_main")
        ],
        [
            InlineKeyboardButton(f"🤖 AutoBuy: {ab_status}", callback_data="grizzly_toggle_autobuy")
        ],
        [
            InlineKeyboardButton("📜 Active Rentals", callback_data="grizzly_active_rentals"),
            InlineKeyboardButton("📋 Valid Numbers", callback_data="grizzly_valid_list")
        ],
        [
            InlineKeyboardButton("📋 Collect Previous (تجميع السابق)", callback_data="grizzly_collect_prev")
        ],
        [
            InlineKeyboardButton("🛒 Manual Rent", callback_data="grizzly_manual_rent_menu"),
            InlineKeyboardButton("⚙️ Settings", callback_data="grizzly_settings")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

def build_settings_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🔑 Edit API Keys", callback_data="grizzly_edit_keys_menu"),
            InlineKeyboardButton("🌍 Set Country ID", callback_data="grizzly_set_country")
        ],
        [
            InlineKeyboardButton("⚙️ Configure APIs", callback_data="grizzly_toggle_providers_menu"),
            InlineKeyboardButton("📋 Edit Services List", callback_data="grizzly_edit_services_menu")
        ],
        [
            InlineKeyboardButton("💵 Set Max Prices", callback_data="grizzly_edit_prices_menu"),
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="grizzly_back_to_main")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

def build_keys_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Grizzly Partner Key", callback_data="grizzly_key_partner")],
        [InlineKeyboardButton("Grizzly User Bearer & Session", callback_data="grizzly_key_user")],
        [InlineKeyboardButton("PVACodes API Key", callback_data="grizzly_key_pvacodes")],
        [InlineKeyboardButton("PVACodes Email", callback_data="grizzly_key_pvaemail")],
        [InlineKeyboardButton("PVACodes Password", callback_data="grizzly_key_pvapassword")],
        [InlineKeyboardButton("OTP Doctor API Key", callback_data="grizzly_key_otpdoctor")],
        [InlineKeyboardButton("🔙 Back to Settings", callback_data="grizzly_settings")]
    ]
    return InlineKeyboardMarkup(buttons)

def build_back_button(target="grizzly_back_to_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=target)]])

def build_autobuy_selector_keyboard(selected_list) -> InlineKeyboardMarkup:
    buttons = []
    for a in ["partner", "user", "pvacodes", "otpdoctor"]:
        checked = "✅" if a in selected_list else "❌"
        buttons.append([InlineKeyboardButton(f"{checked} {a.upper()}", callback_data=f"grizzly_ab_select_{a}")])
    buttons.append([
        InlineKeyboardButton("🟢 Start AutoBuy", callback_data="grizzly_ab_start_confirm"),
        InlineKeyboardButton("🔙 Cancel", callback_data="grizzly_back_to_main")
    ])
    return InlineKeyboardMarkup(buttons)

# --- TELEGRAM COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    txt = (
        "🦁 <b>Grizzly SMS Advanced Control Center</b>\n\n"
        "Welcome to the premium background control bot. Manage your virtual numbers, "
        "check balances, and toggle automated loops in real-time."
    )
    reply_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("/panel"), KeyboardButton("/balances")],
         [KeyboardButton("/autobuy_start"), KeyboardButton("/autobuy_stop")]],
        resize_keyboard=True
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=reply_kb)
    await update.message.reply_text("Select an option from the panel below:", reply_markup=build_main_keyboard())

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text("🦁 <b>Grizzly SMS Control Center</b>", parse_mode="HTML", reply_markup=build_main_keyboard())

async def balances_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    msg = await update.message.reply_text("⏳ Fetching live provider balances...")
    cfg = load_grizzly_config()
    keys = cfg.get("api_keys", {})
    
    partner = await get_provider_balance("partner", keys)
    user = await get_provider_balance("user", keys)
    pva = await get_provider_balance("pvacodes", keys)
    otp = await get_provider_balance("otpdoctor", keys)
    
    txt = (
        "💰 <b>Live Provider Balances</b>\n\n"
        f"• <b>Grizzly Partner:</b> ${partner:.3f}\n"
        f"• <b>Grizzly User:</b> ${user:.3f}\n"
        f"• <b>PVACodes:</b> ${pva:.3f} (Credits)\n"
        f"• <b>OTP Doctor:</b> ₹{otp:.2f}\n\n"
        f"Refreshed: {datetime.now().strftime('%H:%M:%S')}"
    )
    await msg.edit_text(txt, parse_mode="HTML", reply_markup=build_back_button())

async def autobuy_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    global is_autobuy_running
    if is_autobuy_running:
        await update.message.reply_text("🤖 AutoBuy is already running.")
        return
    success = await start_autobuy_system(context.bot)
    if success:
        await update.message.reply_text("🟢 AutoBuy loops started successfully!")
    else:
        await update.message.reply_text("❌ Failed to start AutoBuy. Make sure APIs are selected in settings.")

async def autobuy_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    global is_autobuy_running
    if not is_autobuy_running:
        await update.message.reply_text("🤖 AutoBuy is already stopped.")
        return
    await stop_autobuy_system()
    await update.message.reply_text("🔴 AutoBuy loops stopped.")

# --- NEW COMMAND: /google <activation_id> ---
async def google_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /google <activation_id>")
        return
    aid = args[0]
    if aid not in active_rentals:
        await update.message.reply_text("Activation ID not found.")
        return
    rental = active_rentals[aid]
    phone = rental.get("phone")
    if not phone or phone.lower() in ("none", "pending"):
        await update.message.reply_text("Phone number not available yet.")
        return
    
    code = rental.get("code")
    if not code:
        # No SMS code yet — send OTP first and let the poller handle the rest
        otp_sent = await jio_send_otp(phone)
        if otp_sent:
            rental["otp_sent_time"] = time.time()
            rental["otp_pending"] = True
            rental["jio_valid"] = True
            cfg = load_grizzly_config()
            save_grizzly_config(cfg)
            await update.message.reply_text(f"📤 OTP sent to +{phone}. Waiting for SMS code... (background poller will handle activation)")
        else:
            await update.message.reply_text("❌ Failed to send OTP to Jio number.")
        return
    
    # We already have a code — run activation directly
    if rental.get("activation_processing"):
        await update.message.reply_text("⏳ Activation is already being processed in the background.")
        return
    
    rental["activation_processing"] = True
    cfg = load_grizzly_config()
    keys = cfg.get("api_keys", {})
    await update.message.reply_text(f"🚀 Starting Google One activation for +{phone} with code {code}...")
    asyncio.create_task(process_google_one_activation(context.bot, aid, rental, keys))

# --- CALLBACK QUERY HANDLERS ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("Access Restricted.")
        return
        
    await query.answer()
    data = query.data
    cfg = load_grizzly_config()
    keys = cfg.get("api_keys", {})
    
    # ── Back to Main ──────────────────────────────────────────────────────────
    if data == "grizzly_back_to_main" or data == "grizzly_refresh_main":
        global is_autobuy_running
        status_ab = "🟢 Running" if is_autobuy_running else "🔴 Stopped"
        act_cnt = len([r for r in active_rentals.values() if r.get("status") not in ("cancelled", "completed")])
        txt = (
            "🦁 <b>Grizzly SMS Control Center</b>\n\n"
            f"• <b>AutoBuy Status:</b> {status_ab}\n"
            f"• <b>Active Rentals:</b> {act_cnt}\n"
            f"• <b>Valid Numbers:</b> {len(valid_numbers_found)}\n\n"
            f"Refreshed: {datetime.now().strftime('%H:%M:%S')}"
        )
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_main_keyboard())
        
    # ── Collect Previous ──────────────────────────────────────────────────────
    elif data == "grizzly_collect_prev":
        global history_since_last_collect
        cfg = load_grizzly_config()
        if not history_since_last_collect:
            await query.message.reply_text("⚠️ No new links generated since last collection (تجميع السابق).")
            return
            
        temp_dir = os.path.dirname(CONFIG_FILE)
        file_path = os.path.join(temp_dir, f"collected_previous_{int(time.time())}.txt")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for lnk in history_since_last_collect:
                    f.write(lnk + "\n")
                    
            logger.info(f"Sending {len(history_since_last_collect)} collected previous links to owner...")
            with open(file_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=OWNER_ID,
                    document=doc,
                    filename="collected_previous.txt",
                    caption=f"📋 Compiled previous links ({len(history_since_last_collect)} links since last collection)."
                )
                
            history_since_last_collect = []
            save_grizzly_config(cfg)
            
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.error(f"Error sending collected previous document: {e}")
            await query.message.reply_text(f"❌ Error sending file: {e}")
        
    # ── Toggle AutoBuy ────────────────────────────────────────────────────────
    elif data == "grizzly_toggle_autobuy":
        if is_autobuy_running:
            await stop_autobuy_system()
            await query.message.reply_text("🔴 AutoBuy stopped.")
            status_ab = "🔴 Stopped"
            act_cnt = len([r for r in active_rentals.values() if r.get("status") not in ("cancelled", "completed")])
            txt = (
                "🦁 <b>Grizzly SMS Control Center</b>\n\n"
                f"• <b>AutoBuy Status:</b> {status_ab}\n"
                f"• <b>Active Rentals:</b> {act_cnt}\n"
                f"• <b>Valid Numbers:</b> {len(valid_numbers_found)}"
            )
            await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_main_keyboard())
        else:
            selected = cfg.get("autobuy_settings", {}).get("selected_apis", [])
            txt = (
                "🤖 <b>Configure AutoBuy Providers</b>\n\n"
                "Select the API suppliers (الموردين) you want to run in this AutoBuy session:\n"
            )
            await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_autobuy_selector_keyboard(selected))

    elif data.startswith("grizzly_ab_select_"):
        prov = data.split("_")[3]
        selected = cfg.get("autobuy_settings", {}).get("selected_apis", [])
        if prov in selected:
            selected.remove(prov)
        else:
            selected.append(prov)
        cfg["autobuy_settings"]["selected_apis"] = selected
        save_grizzly_config(cfg)
        
        txt = (
            "🤖 <b>Configure AutoBuy Providers</b>\n\n"
            "Select the API suppliers (الموردين) you want to run in this AutoBuy session:\n"
        )
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_autobuy_selector_keyboard(selected))

    elif data == "grizzly_ab_start_confirm":
        success = await start_autobuy_system(context.bot)
        if success:
            await query.message.reply_text("🟢 AutoBuy started!")
        else:
            await query.message.reply_text("❌ AutoBuy failed to start (make sure at least one provider is selected).")
        
        status_ab = "🟢 Running" if is_autobuy_running else "🔴 Stopped"
        act_cnt = len([r for r in active_rentals.values() if r.get("status") not in ("cancelled", "completed")])
        txt = (
            "🦁 <b>Grizzly SMS Control Center</b>\n\n"
            f"• <b>AutoBuy Status:</b> {status_ab}\n"
            f"• <b>Active Rentals:</b> {act_cnt}\n"
            f"• <b>Valid Numbers:</b> {len(valid_numbers_found)}"
        )
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_main_keyboard())

    # ── Check Balances ────────────────────────────────────────────────────────
    elif data == "grizzly_check_bal":
        await query.message.edit_text("⏳ Querying balances from all 4 providers...")
        partner = await get_provider_balance("partner", keys)
        user = await get_provider_balance("user", keys)
        pva = await get_provider_balance("pvacodes", keys)
        otp = await get_provider_balance("otpdoctor", keys)
        txt = (
            "💰 <b>Live Provider Balances</b>\n\n"
            f"• <b>Grizzly Partner:</b> ${partner:.3f}\n"
            f"• <b>Grizzly User:</b> ${user:.3f}\n"
            f"• <b>PVACodes:</b> ${pva:.3f} (Credits)\n"
            f"• <b>OTP Doctor:</b> ₹{otp:.2f}\n\n"
            f"Refreshed: {datetime.now().strftime('%H:%M:%S')}"
        )
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_back_button())

    # ── Valid List ────────────────────────────────────────────────────────────
    elif data == "grizzly_valid_list":
        if not valid_numbers_found:
            await query.message.edit_text("📋 No valid Jio numbers found in this session yet.", reply_markup=build_back_button())
            return
        txt = "📋 <b>Valid Jio Numbers Found (AutoBuy):</b>\n\n"
        for idx, item in enumerate(valid_numbers_found[-20:]):
            txt += f"{idx+1}. <code>+{item['phone']}</code> | {item['provider']} | <i>{item['time']}</i>\n"
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_back_button())

    # ── Active Rentals List ───────────────────────────────────────────────────
    elif data == "grizzly_active_rentals":
        act = [r for r in active_rentals.values() if r.get("status") not in ("cancelled", "completed")]
        if not act:
            await query.message.edit_text("📜 No active number rentals currently.", reply_markup=build_back_button())
            return
        
        txt = "📜 <b>Active Rentals</b> (Live countdown & SMS codes):\n\n"
        now = time.time()
        buttons = []
        for idx, r in enumerate(act):
            rem_s = int(max(0, r["endTime"] - now))
            mins = rem_s // 60
            secs = rem_s % 60
            code_str = f"🔑 <code>{r['code']}</code>" if r.get("code") else "⏳ waiting SMS"
            txt += f"{idx+1}. <code>+{r['phone']}</code> ({r['provider']})\n"
            txt += f"   ID: <code>{r['id']}</code> | Time: {mins:02d}:{secs:02d} | {code_str}\n\n"
            
            buttons.append([
                InlineKeyboardButton(f"❌ Cancel +{r['phone'][-4:]}", callback_data=f"grizzly_cancel_{r['providerClass']}_{r['id']}"),
                InlineKeyboardButton(f"✅ Complete +{r['phone'][-4:]}", callback_data=f"grizzly_comp_{r['providerClass']}_{r['id']}")
            ])
            
        buttons.append([InlineKeyboardButton("🔄 Refresh List", callback_data="grizzly_active_rentals")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="grizzly_back_to_main")])
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    # ── Manual Cancel / Complete Callbacks ────────────────────────────────────
    elif data.startswith("grizzly_cancel_"):
        # Format: grizzly_cancel_<provider>_<id>
        remainder = data[len("grizzly_cancel_"):]
        sep_idx = remainder.rfind("_")
        prov = remainder[:sep_idx]
        act_id = remainder[sep_idx+1:]
        success = await cancel_number(prov, act_id, keys)
        if success:
            if act_id in active_rentals:
                active_rentals[act_id]["status"] = "cancelled"
                active_rentals[act_id]["removedAt"] = time.time()
            save_grizzly_config(cfg)
            await query.message.reply_text(f"✅ Successfully Cancelled lease ID {act_id}!")
        else:
            await query.message.reply_text(f"❌ Cancellation failed for ID {act_id}.")
        # Refresh active list
        await refresh_active_rentals(keys)
        query.data = "grizzly_active_rentals"
        await callback_query_handler(update, context)

    elif data.startswith("grizzly_comp_"):
        # Format: grizzly_comp_<provider>_<id>
        remainder = data[len("grizzly_comp_"):]
        sep_idx = remainder.rfind("_")
        prov = remainder[:sep_idx]
        act_id = remainder[sep_idx+1:]
        success = await complete_number(prov, act_id, keys)
        if success:
            if act_id in active_rentals:
                active_rentals[act_id]["status"] = "completed"
                active_rentals[act_id]["removedAt"] = time.time()
            save_grizzly_config(cfg)
            await query.message.reply_text(f"✅ Successfully Completed lease ID {act_id}!")
        else:
            await query.message.reply_text(f"❌ Complete call failed for ID {act_id}.")
        await refresh_active_rentals(keys)
        query.data = "grizzly_active_rentals"
        await callback_query_handler(update, context)

    # ── Settings Main Menu ────────────────────────────────────────────────────
    elif data == "grizzly_settings":
        txt = (
            "⚙️ <b>Grizzly SMS Bot Settings</b>\n\n"
            f"🌍 Country ID: <code>{cfg.get('autobuy_settings', {}).get('country_id', '22')}</code>\n"
            f"Selected APIs: <code>{', '.join(cfg.get('autobuy_settings', {}).get('selected_apis', []))}</code>\n\n"
            "Use the buttons below to change API credentials, services, country, or loop configuration."
        )
        await query.message.edit_text(txt, parse_mode="HTML", reply_markup=build_settings_keyboard())

    # ── Edit API Keys sub-menu ────────────────────────────────────────────────
    elif data == "grizzly_edit_keys_menu":
        await query.message.edit_text("🔑 <b>Edit API Credentials:</b>", parse_mode="HTML", reply_markup=build_keys_keyboard())

    elif data.startswith("grizzly_key_"):
        key_type = data.split("_")[2]
        prompt_map = {
            "partner": "Send Grizzly Partner API Key (e.g. 632344...):",
            "user": "Send Grizzly User Bearer Token & Session separated by space:\n\n<code>BEARER_TOKEN SESSION_TOKEN</code>",
            "pvacodes": "Send PVACodes Key (e.g. 6a18ec...):",
            "pvaemail": "Send PVACodes Account Email:",
            "pvapassword": "Send PVACodes Account Password:",
            "otpdoctor": "Send OTP Doctor API Key (e.g. p5lqci...):"
        }
        context.user_data["admin_action"] = f"grizzly_save_key_{key_type}"
        await query.message.edit_text(
            prompt_map.get(key_type, "Send value:"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="grizzly_edit_keys_menu")]])
        )

    # ── Set Country ID ────────────────────────────────────────────────────────
    elif data == "grizzly_set_country":
        context.user_data["admin_action"] = "grizzly_save_country"
        await query.message.edit_text(
            "🌍 Enter Country ID for renting/autobuy:\n\n"
            "• India = <code>22</code>\n"
            "• Russia = <code>0</code>\n"
            "• USA = <code>12</code>\n"
            "• UK = <code>15</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="grizzly_settings")]])
        )

    # ── Toggle Provider APIs ──────────────────────────────────────────────────
    elif data == "grizzly_toggle_providers_menu":
        selected = cfg.get("autobuy_settings", {}).get("selected_apis", [])
        buttons = []
        for a in ["partner", "user", "pvacodes", "otpdoctor"]:
            checked = "✅" if a in selected else "❌"
            buttons.append([InlineKeyboardButton(f"{checked} {a.upper()}", callback_data=f"grizzly_toggle_prov_{a}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="grizzly_settings")])
        await query.message.edit_text("⚙️ <b>Toggle APIs for AutoBuy:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("grizzly_toggle_prov_"):
        prov = data.split("_")[3]
        selected = cfg.get("autobuy_settings", {}).get("selected_apis", [])
        if prov in selected:
            selected.remove(prov)
        else:
            selected.append(prov)
        cfg["autobuy_settings"]["selected_apis"] = selected
        save_grizzly_config(cfg)
        query.data = "grizzly_toggle_providers_menu"
        await callback_query_handler(update, context)

    # ── Edit Services list sub-menu ───────────────────────────────────────────
    elif data == "grizzly_edit_services_menu":
        buttons = [
            [InlineKeyboardButton("Partner Services", callback_data="grizzly_edit_svc_partner")],
            [InlineKeyboardButton("User Services", callback_data="grizzly_edit_svc_user")],
            [InlineKeyboardButton("PVACodes Services", callback_data="grizzly_edit_svc_pvacodes")],
            [InlineKeyboardButton("OTP Doctor Services", callback_data="grizzly_edit_svc_otpdoctor")],
            [InlineKeyboardButton("🔙 Back to Settings", callback_data="grizzly_settings")]
        ]
        await query.message.edit_text("⚙️ <b>Select provider to configure services list:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("grizzly_edit_svc_"):
        prov = data.split("_")[3]
        current_svc = cfg.get("autobuy_settings", {}).get("services", {}).get(prov, "")
        context.user_data["admin_action"] = f"grizzly_save_services_{prov}"
        await query.message.edit_text(
            f"⚙️ Configure Services for <b>{prov.upper()}</b>\n\n"
            f"Current: <code>{current_svc}</code>\n\n"
            "Send service codes separated by comma (e.g. <code>Jio5, Jio10, Jio11</code>):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="grizzly_edit_services_menu")]])
        )

    # ── Edit Max Prices sub-menu ──────────────────────────────────────────────
    elif data == "grizzly_edit_prices_menu":
        buttons = [
            [InlineKeyboardButton("Partner Max Price", callback_data="grizzly_edit_price_partner")],
            [InlineKeyboardButton("User Max Price", callback_data="grizzly_edit_price_user")],
            [InlineKeyboardButton("PVACodes Max Price", callback_data="grizzly_edit_price_pvacodes")],
            [InlineKeyboardButton("OTP Doctor Max Price", callback_data="grizzly_edit_price_otpdoctor")],
            [InlineKeyboardButton("🔙 Back to Settings", callback_data="grizzly_settings")]
        ]
        await query.message.edit_text("⚙️ <b>Select provider to configure max purchase price:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("grizzly_edit_price_"):
        prov = data.split("_")[3]
        current_price = cfg.get("autobuy_settings", {}).get("max_prices", {}).get(prov, 0.0)
        context.user_data["admin_action"] = f"grizzly_save_price_{prov}"
        await query.message.edit_text(
            f"⚙️ Configure Max Price for <b>{prov.upper()}</b>\n\n"
            f"Current Max Price: <code>{current_price}</code>\n\n"
            "Send new max price (e.g. <code>0.135</code> for USD, or <code>20.0</code> for INR):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="grizzly_edit_prices_menu")]])
        )

    # ── Manual Rent Menu ──────────────────────────────────────────────────────
    elif data == "grizzly_manual_rent_menu":
        buttons = []
        for a in ["partner", "user", "pvacodes", "otpdoctor"]:
            buttons.append([InlineKeyboardButton(f"Rent via {a.upper()}", callback_data=f"grizzly_rent_prov_{a}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="grizzly_back_to_main")])
        await query.message.edit_text("🛒 <b>Manual Rent Number:</b>\nSelect API Provider:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("grizzly_rent_prov_"):
        prov = data.split("_")[3]
        context.user_data["rent_provider"] = prov
        country = cfg.get("autobuy_settings", {}).get("country_id", "22")
        context.user_data["rent_country"] = country
        context.user_data["admin_action"] = "grizzly_rent_service"
        await query.message.edit_text(
            f"🛒 Rent via <b>{prov.upper()}</b> (Country: {country})\n\n"
            "Send service code to order (e.g. <code>Jio5</code> or <code>Jio11</code>):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="grizzly_manual_rent_menu")]])
        )

# --- TEXT MESSAGE HANDLER FOR INPUTS ---
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    action = context.user_data.get("admin_action")
    if not action:
        return
        
    context.user_data["admin_action"] = None
    text = update.message.text.strip()
    cfg = load_grizzly_config()
    
    # ── Save API Key Credentials ──────────────────────────────────────────────
    if action.startswith("grizzly_save_key_"):
        key_type = action.split("_")[3]
        if key_type == "user":
            parts = text.split()
            if len(parts) >= 2:
                cfg["api_keys"]["bearerToken"] = parts[0]
                cfg["api_keys"]["sessionToken"] = parts[1]
                save_grizzly_config(cfg)
                await update.message.reply_text("✅ Grizzly User Credentials saved successfully!", reply_markup=build_keys_keyboard())
            else:
                await update.message.reply_text("❌ Format error. Send token and session space-separated:", reply_markup=build_keys_keyboard())
        else:
            key_map = {
                "partner": "partner",
                "pvacodes": "pvacodesKey",
                "pvaemail": "pvacodesEmail",
                "pvapassword": "pvacodesPassword",
                "pvacookie": "pvacodesCookie",
                "otpdoctor": "otpdoctorKey"
            }
            cfg["api_keys"][key_map[key_type]] = text
            save_grizzly_config(cfg)
            await update.message.reply_text(f"✅ {key_type.upper()} saved successfully!", reply_markup=build_keys_keyboard())

    # ── Save Country ID ───────────────────────────────────────────────────────
    elif action == "grizzly_save_country":
        cfg["autobuy_settings"]["country_id"] = text
        save_grizzly_config(cfg)
        await update.message.reply_text(f"✅ Country ID updated to: <code>{text}</code>", parse_mode="HTML", reply_markup=build_settings_keyboard())

    # ── Save Services list ────────────────────────────────────────────────────
    elif action.startswith("grizzly_save_services_"):
        prov = action.split("_")[3]
        cfg["autobuy_settings"]["services"][prov] = text
        save_grizzly_config(cfg)
        await update.message.reply_text(f"✅ Services list for {prov.upper()} saved!", reply_markup=build_settings_keyboard())

    # ── Save Max Price ────────────────────────────────────────────────────────
    elif action.startswith("grizzly_save_price_"):
        prov = action.split("_")[3]
        try:
            val = float(text)
            cfg["autobuy_settings"]["max_prices"][prov] = val
            save_grizzly_config(cfg)
            await update.message.reply_text(f"✅ Max price for {prov.upper()} updated to {val}!", reply_markup=build_settings_keyboard())
        except ValueError:
            await update.message.reply_text("❌ Invalid price format. Must be a decimal number (e.g. 0.135).", reply_markup=build_settings_keyboard())

    # ── Manual Order Renting ──────────────────────────────────────────────────
    elif action == "grizzly_rent_service":
        prov = context.user_data.get("rent_provider")
        country = context.user_data.get("rent_country", "22")
        service = text
        
        await update.message.reply_text(f"⏳ Attempting to rent virtual number for <b>{service}</b> via <b>{prov.upper()}</b>...", parse_mode="HTML")
        
        keys = cfg.get("api_keys", {})
        max_p = float(cfg.get("autobuy_settings", {}).get("max_prices", {}).get(prov, 0.135))
        
        order = await rent_number(prov, country, service, max_p, keys)
        if order.get("ok"):
            phone = order["phone"]
            act_id = order["id"]
            cost = order["cost"]
            
            prov_name = "Grizzly Partner" if prov == "partner" else ("Grizzly User" if prov == "user" else prov.upper())
            
            active_rentals[act_id] = {
                "id": act_id,
                "phone": phone,
                "cost": cost,
                "service": service,
                "country": country,
                "status": "active",
                "code": "",
                "provider": prov_name,
                "providerClass": prov,
                "endTime": time.time() + 900
            }
            save_grizzly_config(cfg)
            
            # Run Jio validation with Google One flow
            await update.message.reply_text(
                f"✅ <b>Number Leased Successfully!</b>\n\n"
                f"• Phone: <code>+{phone}</code>\n"
                f"• ID: <code>{act_id}</code>\n"
                f"• Cost: {cost}\n\n"
                "Checking Jio status...",
                parse_mode="HTML"
            )
            
            is_valid = await check_jio_number(phone)
            if is_valid:
                if act_id in active_rentals:
                    active_rentals[act_id]["jio_valid"] = True
                await update.message.reply_text(
                    f"✅ <b>Jio Status: VALID!</b>\n\n"
                    "Number added to active list and polling for SMS.",
                    parse_mode="HTML"
                )
                # Trigger Google One activation
                if cfg.get("google_one_activation", True):
                    otp_sent = await jio_send_otp(phone)
                    if otp_sent:
                        if act_id in active_rentals:
                            active_rentals[act_id]["otp_sent_time"] = time.time()
                            active_rentals[act_id]["otp_pending"] = True
                            save_grizzly_config(cfg)
                        await update.message.reply_text(f"📤 OTP sent to +{phone}. Waiting for SMS... (up to 60s)")
                        # We'll rely on the background poller to handle the OTP and then validate
                    else:
                        await update.message.reply_text("❌ Failed to send OTP.")
                else:
                    await update.message.reply_text("✅ Number is valid. Google One activation disabled.")
            else:
                wait_s = 5 if (prov == "pvacodes" or prov == "otpdoctor") else 120
                await update.message.reply_text(
                    f"❌ <b>Jio Status: INVALID!</b>\n\n"
                    f"Number will be auto-cancelled in {wait_s} seconds.",
                    parse_mode="HTML"
                )
                await asyncio.sleep(wait_s)
                await cancel_number(prov, act_id, keys, number=phone)
                if act_id in active_rentals:
                    active_rentals[act_id]["status"] = "cancelled"
                    active_rentals[act_id]["removedAt"] = time.time()
                save_grizzly_config(cfg)
                await update.message.reply_text(f"❌ Cancelled invalid lease ID {act_id}.")
        else:
            await update.message.reply_text(f"❌ Lease failed: <code>{order.get('error')}</code>", parse_mode="HTML", reply_markup=build_back_button())

# --- MAIN ---
from telegram.ext import ExtBot

class RobustBot(ExtBot):
    async def send_message(self, *args, **kwargs):
        try:
            return await super().send_message(*args, **kwargs)
        except Exception as e:
            logger.error(f"Telegram send_message failed: {e}")

    async def send_document(self, *args, **kwargs):
        try:
            return await super().send_document(*args, **kwargs)
        except Exception as e:
            logger.error(f"Telegram send_document failed: {e}")

def main():
    logger.info("🚀 Initializing Grizzly SMS Telegram Bot Control Panel...")
    cfg = load_grizzly_config()
    app = Application.builder().bot(RobustBot(token=BOT_TOKEN)).build()
    
    # Register Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CommandHandler("balances", balances_command))
    app.add_handler(CommandHandler("autobuy_start", autobuy_start_command))
    app.add_handler(CommandHandler("autobuy_stop", autobuy_stop_command))
    app.add_handler(CommandHandler("google", google_command))  # NEW command
    
    # Register Callback Queries & Messages
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    
    # Retrieve Event Loop and Start Background SMS polling loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(grizzly_poller_task(app.bot))
    
    # Ensure AutoBuy is started on startup
    cfg["is_autobuy_running"] = True
    save_grizzly_config(cfg)
    loop.create_task(start_autobuy_system(app.bot))
        
    logger.info("🟢 Grizzly SMS Bot started successfully and listening on Telegram.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
