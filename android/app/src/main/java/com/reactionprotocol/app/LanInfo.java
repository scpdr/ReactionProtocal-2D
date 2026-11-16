package com.reactionprotocol.app;

import android.content.Context;
import android.net.wifi.WifiManager;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;

/**
 * 优选局域网地址：手机同时开 WiFi 和移动数据时，默认路由可能是蜂窝网，
 * Python 侧 lan_ips() 可能给出运营商内网 IP，同学是连不上的。
 * 这里直接从 WLAN/以太网接口取 IPv4，并把 WiFi 地址排在最前。
 */
public final class LanInfo {

    private LanInfo() {
    }

    public static List<String> addresses(Context ctx) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        try {
            WifiManager wm = (WifiManager) ctx.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            if (wm != null && wm.getConnectionInfo() != null) {
                int ip = wm.getConnectionInfo().getIpAddress();
                if (ip != 0) {
                    out.add(intToIp(ip));
                }
            }
        } catch (Throwable ignored) {
        }
        try {
            Enumeration<NetworkInterface> ifs = NetworkInterface.getNetworkInterfaces();
            while (ifs != null && ifs.hasMoreElements()) {
                NetworkInterface ni = ifs.nextElement();
                String name = ni.getName() == null ? "" : ni.getName().toLowerCase();
                if (name.startsWith("wlan") || name.startsWith("ap") || name.startsWith("eth")) {
                    for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                        if (a instanceof Inet4Address && !a.isLoopbackAddress()) {
                            out.add(a.getHostAddress());
                        }
                    }
                }
            }
        } catch (Throwable ignored) {
        }
        return new ArrayList<>(out);
    }

    private static String intToIp(int ip) {
        return (ip & 0xff) + "." + ((ip >> 8) & 0xff) + "." + ((ip >> 16) & 0xff) + "." + ((ip >> 24) & 0xff);
    }
}
