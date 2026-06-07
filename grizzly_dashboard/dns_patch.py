# -*- coding: utf-8 -*-
"""
DNS Monkey Patch for socket.getaddrinfo
Bypasses the broken/misconfigured IPv6 resolver on Windows by querying
public DNS servers (8.8.8.8 and 1.1.1.1) via UDP first.
"""

import socket
import struct
import logging

logger = logging.getLogger("DNS_Patch")

_orig_getaddrinfo = socket.getaddrinfo

def custom_dns_resolve_udp(hostname, dns_servers=["8.8.8.8", "1.1.1.1", "8.8.4.4"]):
    if not hostname:
        return None
        
    try:
        socket.inet_aton(hostname)
        return hostname
    except socket.error:
        pass
        
    if (hostname == "localhost" or 
        hostname.startswith("127.") or 
        hostname.endswith(".local") or 
        "." not in hostname):
        return None

    try:
        packet = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        for part in hostname.split('.'):
            if not part:
                continue
            packet += struct.pack("B", len(part)) + part.encode('utf-8')
        packet += b'\x00'
        packet += struct.pack(">HH", 1, 1)
    except Exception as e:
        logger.debug(f"Failed to build DNS packet for {hostname}: {e}")
        return None
    
    for dns_server in dns_servers:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.5)
        try:
            sock.sendto(packet, (dns_server, 53))
            data, _ = sock.recvfrom(512)
            
            if len(data) < 12:
                continue
            tx_id, flags, q_count, ans_count, auth_count, add_count = struct.unpack(">HHHHHH", data[:12])
            
            if (flags & 0x000F) != 0 or ans_count == 0:
                continue
                
            idx = 12
            for _ in range(q_count):
                while True:
                    if idx >= len(data):
                        break
                    length = data[idx]
                    if length == 0:
                        idx += 1
                        break
                    idx += 1 + length
                idx += 4
                
            for _ in range(ans_count):
                if idx >= len(data):
                    break
                if (data[idx] & 0xC0) == 0xC0:
                    idx += 2
                else:
                    while True:
                        if idx >= len(data):
                            break
                        length = data[idx]
                        if length == 0:
                            idx += 1
                            break
                        idx += 1 + length
                        
                if idx + 10 > len(data):
                    break
                atype, aclass, attl, rdlen = struct.unpack(">HHIH", data[idx:idx+10])
                idx += 10
                
                if idx + rdlen > len(data):
                    break
                
                if atype == 1 and rdlen == 4:
                    ip = socket.inet_ntoa(data[idx:idx+4])
                    return ip
                idx += rdlen
        except Exception:
            pass
        finally:
            sock.close()
            
    return None

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if isinstance(host, bytes):
        host = host.decode('utf-8')
    try:
        ip = custom_dns_resolve_udp(host)
        if ip:
            if ":" not in ip and family == socket.AF_INET6:
                family = socket.AF_INET
            return _orig_getaddrinfo(ip, port, family, type, proto, flags)
    except Exception:
        pass
    if host == 'api.telegram.org' and family == socket.AF_INET6:
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = patched_getaddrinfo
logger.info("⚡ DNS Monkey Patch applied successfully (using public resolvers 8.8.8.8/1.1.1.1 via UDP).")
