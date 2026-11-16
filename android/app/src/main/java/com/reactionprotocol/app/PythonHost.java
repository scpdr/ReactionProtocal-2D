package com.reactionprotocol.app;

import android.content.Context;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * APK 内嵌 Python 权威服务端（Chaquopy）。
 *
 * 手机的 APK 里带着 server.py + game/ + data/ + public/：
 *   1) 首启（或版本升级后）把 assets 里的 public/、data/ 解包到 filesDir/host；
 *   2) 启动 Chaquopy 的 CPython，调用 host_runner.serve(root, port, bots, True)；
 *   3) 手机自己监听 0.0.0.0:8765，其他手机/电脑连它的局域网 IP 即可加入。
 */
public final class PythonHost {

    private static final String TAG = "RPWeb";
    public static final int DEFAULT_PORT = 8765;
    private static final String STAMP = ".asset_version";

    private static final Object LOCK = new Object();
    private static volatile int port = 0;
    private static volatile String ips = "";
    private static volatile String lastError = "";

    private PythonHost() {
    }

    /** 启动内嵌服务端。返回 "端口|ip1,ip2"，失败返回 "ERROR 原因"。 */
    public static String start(Context ctx, int wantPort, int bots, int mapSwitchSeconds) {
        synchronized (LOCK) {
            try {
                File root = extractAssets(ctx);
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(ctx));
                }
                PyObject mod = Python.getInstance().getModule("host_runner");
                String info = String.valueOf(mod.callAttr("serve", root.getAbsolutePath(),
                        wantPort, bots, true, mapSwitchSeconds > 0 ? mapSwitchSeconds : null));
                parse(info);
                Log.i(TAG, "host start -> " + info);
                return info;
            } catch (Throwable t) {
                lastError = t.getClass().getSimpleName() + ": " + t.getMessage();
                port = 0;
                ips = "";
                Log.e(TAG, "host start failed", t);
                return "ERROR " + lastError;
            }
        }
    }

    /** 停止内嵌服务端（释放 8765 端口）。 */
    public static void stop() {
        synchronized (LOCK) {
            try {
                if (Python.isStarted()) {
                    Python.getInstance().getModule("host_runner").callAttr("stop");
                }
            } catch (Throwable t) {
                Log.w(TAG, "host stop: " + t);
            }
            port = 0;
            ips = "";
        }
    }

    public static boolean isRunning() {
        return port > 0;
    }

    public static int getPort() {
        return port;
    }

    /** 局域网地址集合（逗号分隔），用于告诉同学连哪个地址。 */
    public static String getIps() {
        return ips;
    }

    public static String lastError() {
        return lastError;
    }

    /** 本机自连地址，交给 WebView 使用。 */
    public static String loopback() {
        return port > 0 ? "127.0.0.1:" + port : "";
    }

    private static void parse(String info) {
        if (info == null || info.startsWith("ERROR")) {
            port = 0;
            ips = "";
            lastError = info == null ? "unknown" : info;
            return;
        }
        String[] parts = info.split("\\|", -1);
        try {
            port = Integer.parseInt(parts[0].trim());
        } catch (Exception e) {
            port = 0;
        }
        ips = parts.length > 1 ? parts[1].trim() : "";
        if (port > 0) {
            lastError = "";
        }
    }

    // ---------------- assets 解包 ----------------

    /** 把 assets/public 与 assets/data 解包到 filesDir/host，版本或安装时间变化时重解。 */
    private static File extractAssets(Context ctx) throws IOException {
        File root = new File(ctx.getFilesDir(), "host");
        File stamp = new File(root, STAMP);
        String want = stampValue(ctx);
        if (stamp.isFile() && want.equals(readText(stamp))) {
            return root;
        }
        deleteRec(root);
        if (!root.mkdirs() && !root.isDirectory()) {
            throw new IOException("cannot create " + root);
        }
        copyAssetTree(ctx, "public", new File(root, "public"));
        copyAssetTree(ctx, "data", new File(root, "data"));
        writeText(stamp, want);
        Log.i(TAG, "assets extracted -> " + root.getAbsolutePath());
        return root;
    }

    /**
     * 解包时间戳 = versionCode + 安装时间。
     * 只用 versionCode 会出事：重装同一个 versionCode 的包（开发期常态）时资源不会刷新，
     * 手机对外提供的 public/ 会一直是旧版（实测过：站点 game.js 落后一个版本）。
     */
    private static String stampValue(Context ctx) {
        long updated = 0L;
        try {
            updated = ctx.getPackageManager().getPackageInfo(ctx.getPackageName(), 0).lastUpdateTime;
        } catch (Throwable ignored) {
        }
        return BuildConfig.VERSION_CODE + "|" + updated;
    }

    private static void copyAssetTree(Context ctx, String assetPath, File dst) throws IOException {
        String[] kids = ctx.getAssets().list(assetPath);
        if (kids == null || kids.length == 0) {
            copyFile(ctx, assetPath, dst);
            return;
        }
        if (!dst.isDirectory() && !dst.mkdirs()) {
            throw new IOException("mkdir " + dst);
        }
        for (String k : kids) {
            copyAssetTree(ctx, assetPath + "/" + k, new File(dst, k));
        }
    }

    private static void copyFile(Context ctx, String assetPath, File dst) throws IOException {
        File parent = dst.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IOException("mkdir " + parent);
        }
        try (InputStream in = ctx.getAssets().open(assetPath);
             OutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
        }
    }

    private static void deleteRec(File f) {
        if (f == null || !f.exists()) {
            return;
        }
        if (f.isDirectory()) {
            File[] kids = f.listFiles();
            if (kids != null) {
                for (File k : kids) {
                    deleteRec(k);
                }
            }
        }
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    private static String readText(File f) {
        try (InputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[256];
            int n;
            while ((n = in.read(buf)) > 0) {
                bos.write(buf, 0, n);
            }
            return new String(bos.toByteArray(), "UTF-8");
        } catch (IOException e) {
            return "";
        }
    }

    private static void writeText(File f, String s) throws IOException {
        try (OutputStream out = new FileOutputStream(f)) {
            out.write(s.getBytes("UTF-8"));
        }
    }
}
