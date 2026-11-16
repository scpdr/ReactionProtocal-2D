package com.reactionprotocol.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.text.InputType;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

/**
 * Reaction Protocol Android 客户端 —— 手机既能开服，也能加入别人的房间。
 *
 * ① 本机开服：APK 内嵌 CPython 跑权威服务端（Chaquopy），本机监听 0.0.0.0:8765；
 *    其他手机/电脑在同一局域网里打开 http://本机IP:8765 即可加入，无需任何电脑。
 * ② 加入房间：填主机地址（手机或电脑的局域网 IP:8765）。
 * 真机自动化验收仍可用：am start --es server 127.0.0.1:8765 / --ez host true。
 */
public class MainActivity extends Activity {

    private static final String TAG = "RPWeb";
    private static final String PREFS = "reaction_protocol";
    private static final int REQ_NOTIF = 88;

    private WebView web;
    private String server = "";
    private String mode = "";                 // "" | host | join
    private int hostMapSwitch = 0;            // 主机换图间隔（秒），0=用服务端默认
    private final int hostPort = PythonHost.DEFAULT_PORT;
    private final Handler ui = new Handler();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        goImmersive();

        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        mode = sp.getString("mode", "");
        server = sp.getString("server", "");

        Intent it = getIntent();
        String extraServer = (it == null) ? null : it.getStringExtra("server");
        boolean extraHost = it != null && it.getBooleanExtra("host", false);
        hostMapSwitch = (it == null) ? 0 : it.getIntExtra("map_switch", 0);

        buildWebView();
        askNotificationPermission();

        if (extraServer != null && !extraServer.trim().isEmpty()) {
            joinGame(extraServer.trim());                      // 兼容既有真机验收脚本
        } else if (extraHost) {
            startHost(4);
        } else if ("host".equals(mode)) {
            startHost(2);                                      // 上次就是主机，直接继续开服
        } else if ("join".equals(mode) && !server.isEmpty()) {
            joinGame(server);
        } else {
            showModeDialog(true);
        }
        startErrorWatchdog();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (intent == null) {
            return;
        }
        String s = intent.getStringExtra("server");
        if (s != null && !s.trim().isEmpty()) {
            joinGame(s.trim());
        } else if (intent.getBooleanExtra("host", false)) {
            startHost(4);
        }
    }

    // ---------------- WebView ----------------

    private void buildWebView() {
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                Log.i(TAG, "console: " + cm.message() + " @" + cm.lineNumber());
                return true;
            }
        });
        web.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View v) {
                showModeDialog(false);
                return true;
            }
        });

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
    }

    private void goImmersive() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    /** 加载触屏客户端；server = 主机地址 host:port。 */
    private void loadGame(String sv) {
        server = sv.trim();
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        sp.edit().putString("server", server).putString("mode", mode).apply();
        String url = "file:///android_asset/mobile.html?server="
                + android.net.Uri.encode(server) + "&v=1.2";
        Log.i(TAG, "load " + url);
        web.loadUrl(url);
    }

    // ---------------- 两种模式 ----------------

    private void showModeDialog(final boolean firstRun) {
        final boolean hosting = PythonHost.isRunning();
        final String[] items = hosting
                ? new String[]{"本机开服（已是主机）", "加入房间（输入主机地址）", "复制我的开服地址", "停止开服"}
                : new String[]{"本机开服（我是主机，手机自己当服务器）", "加入房间（输入主机地址）",
                               "单机试玩（本机开服 + 4 个 AI 对手）"};
        new AlertDialog.Builder(this)
                .setTitle(hosting ? "主机模式运行中" : "开始游戏")
                .setItems(items, (d, which) -> {
                    if (hosting) {
                        if (which == 0) {
                            loadGame("127.0.0.1:" + PythonHost.getPort());
                        } else if (which == 1) {
                            askServer();
                        } else if (which == 2) {
                            copyAddress();
                        } else {
                            stopHost();
                        }
                    } else {
                        if (which == 0) {
                            startHost(2);
                        } else if (which == 1) {
                            askServer();
                        } else {
                            startHost(4);
                        }
                    }
                })
                .setNegativeButton("取消", (d, w) -> {
                    if (firstRun) {
                        Toast.makeText(this, "长按屏幕可重新选择模式", Toast.LENGTH_LONG).show();
                    }
                })
                .show();
    }

    private void askServer() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("192.168.1.5:8765");
        input.setText(server == null || server.isEmpty() ? "127.0.0.1:8765" : server);
        new AlertDialog.Builder(this)
                .setTitle("加入房间")
                .setMessage("填主机的局域网地址（手机或电脑上显示的那个）\n同一 WiFi 才能连上；电脑 USB 调试可用 127.0.0.1:8765")
                .setView(input)
                .setPositiveButton("连接", (d, w) -> {
                    mode = "join";
                    joinGame(input.getText().toString());
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void joinGame(String sv) {
        if (sv == null || sv.trim().isEmpty()) {
            Toast.makeText(this, "地址为空，长按屏幕重新选择", Toast.LENGTH_LONG).show();
            return;
        }
        mode = "join";
        loadGame(sv.trim());
    }

    /** 开服：拉起前台服务（内嵌 Python），等端口就绪后再进游戏。 */
    private void startHost(int bots) {
        mode = "host";
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("mode", mode).apply();
        Toast.makeText(this, "正在本机开服（手机将作为服务器）…", Toast.LENGTH_SHORT).show();
        Intent svc = new Intent(this, HostService.class);
        svc.setAction(HostService.ACTION_START);
        svc.putExtra(HostService.EXTRA_PORT, hostPort);
        svc.putExtra(HostService.EXTRA_BOTS, bots);
        if (hostMapSwitch > 0) {
            svc.putExtra(HostService.EXTRA_MAP_SWITCH, hostMapSwitch);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
        waitHostAndLoad(0);
    }

    private void stopHost() {
        Intent svc = new Intent(this, HostService.class);
        svc.setAction(HostService.ACTION_STOP);
        startService(svc);
        PythonHost.stop();
        Toast.makeText(this, "已停止开服", Toast.LENGTH_SHORT).show();
        showModeDialog(false);
    }

    private void waitHostAndLoad(final int tries) {
        if (PythonHost.isRunning()) {
            loadGame("127.0.0.1:" + PythonHost.getPort());
            ui.postDelayed(this::showShareDialog, 1200);
            return;
        }
        if (tries > 130) {                                 // ≈40s（首启要解包资源+启动 CPython，实测十余秒）
            String err = PythonHost.lastError();
            Toast.makeText(this, "开服失败：" + err, Toast.LENGTH_LONG).show();
            showModeDialog(false);
            return;
        }
        ui.postDelayed(() -> waitHostAndLoad(tries + 1), 300);
    }

    private String shareLines() {
        StringBuilder sb = new StringBuilder();
        int port = PythonHost.getPort() > 0 ? PythonHost.getPort() : hostPort;
        java.util.List<String> ips = LanInfo.addresses(this);       // WiFi/以太网优先
        if (ips.isEmpty()) {
            String pyIps = PythonHost.getIps();                    // 兜底：Python 侧探测结果
            if (pyIps != null && !pyIps.isEmpty()) {
                for (String ip : pyIps.split(",")) {
                    if (!ip.trim().isEmpty()) {
                        ips.add(ip.trim());
                    }
                }
            }
        }
        for (String ip : ips) {
            sb.append("http://").append(ip).append(":").append(port).append("\n");
        }
        sb.append("（本机自连 127.0.0.1:").append(port).append("）");
        return sb.toString();
    }

    private void showShareDialog() {
        if (!PythonHost.isRunning()) {
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("主机已开服 · 把地址发给同学")
                .setMessage("同一 WiFi 下，同学用手机浏览器或本 APP 的“加入房间”填：\n\n"
                        + shareLines() + "\n\n本机 AI 机器人已加入，可先自己打。")
                .setPositiveButton("复制地址", (d, w) -> copyAddress())
                .setNegativeButton("开始玩", null)
                .show();
    }

    private void copyAddress() {
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(ClipData.newPlainText("ReactionProtocol", shareLines()));
            Toast.makeText(this, "地址已复制", Toast.LENGTH_SHORT).show();
        }
    }

    private void askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
        }
    }

    /** 把 WebView 里的 JS 报错定期打到 logcat，真机验收时可直接抓取。 */
    private void startErrorWatchdog() {
        ui.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (web == null) {
                    return;
                }
                web.evaluateJavascript("JSON.stringify(window.__errors||[])", new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String v) {
                        if (v != null && !"[]".equals(v) && !"null".equals(v) && !"\"[]\"".equals(v)) {
                            Log.e(TAG, "JS_ERRORS " + v);
                        }
                    }
                });
                ui.postDelayed(this, 3000);
            }
        }, 3000);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            goImmersive();
        }
    }
}
