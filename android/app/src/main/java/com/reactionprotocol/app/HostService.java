package com.reactionprotocol.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;
import android.widget.Toast;

/**
 * 主机前台服务：在后台保持 Python 权威服务端运行（息屏/切后台也不掉线）。
 * 通知栏常驻「主机已开服 · 8765」并提供“停止开服”。
 */
public class HostService extends Service {

    private static final String TAG = "RPWeb";
    public static final String ACTION_START = "com.reactionprotocol.app.action.START_HOST";
    public static final String ACTION_STOP = "com.reactionprotocol.app.action.STOP_HOST";
    public static final String EXTRA_PORT = "port";
    public static final String EXTRA_BOTS = "bots";
    public static final String EXTRA_MAP_SWITCH = "map_switch";

    private static final String CHANNEL_ID = "rp_host";
    private static final int NOTIF_ID = 8765;

    private PowerManager.WakeLock wakeLock;
    private Thread worker;
    private volatile boolean stopping = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "rp:host");
            wakeLock.setReferenceCounted(false);
            try {
                wakeLock.acquire();
            } catch (Exception e) {
                Log.w(TAG, "wakelock: " + e);
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            return handleStart(intent);
        } catch (Throwable t) {
            Log.e(TAG, "onStartCommand failed", t);
            Toast.makeText(this, "开服失败：" + t, Toast.LENGTH_LONG).show();
            stopSelf();
            return START_NOT_STICKY;
        }
    }

    private int handleStart(Intent intent) {
        String action = (intent == null) ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopHost();
            stopSelf();
            return START_NOT_STICKY;
        }
        final int port = (intent == null) ? PythonHost.DEFAULT_PORT
                : intent.getIntExtra(EXTRA_PORT, PythonHost.DEFAULT_PORT);
        final int bots = (intent == null) ? 2 : intent.getIntExtra(EXTRA_BOTS, 2);
        final int mapSwitch = (intent == null) ? 0 : intent.getIntExtra(EXTRA_MAP_SWITCH, 0);

        startForeground(NOTIF_ID, notification("正在启动主机…", "手机正在变成游戏服务器"));
        if (PythonHost.isRunning()) {
            updateNotification("主机已开服 · 端口 " + PythonHost.getPort(), addressText() + powerHint());
            return START_STICKY;
        }
        if (worker == null || !worker.isAlive()) {
            worker = new Thread(new Runnable() {
                @Override
                public void run() {
                    String info = PythonHost.start(getApplicationContext(), port, bots, mapSwitch);
                    if (stopping) {
                        return;
                    }
                    if (PythonHost.isRunning()) {
                        updateNotification("主机已开服 · 端口 " + PythonHost.getPort(),
                                addressText() + powerHint());
                    } else {
                        updateNotification("开服失败", info);
                    }
                }
            }, "rp-host");
            worker.start();
        }
        return START_STICKY;
    }

    /** 电量/发热提示：开服是持续占用 CPU 的，低电或过热时给玩家一句提醒。 */
    private String powerHint() {
        try {
            Intent bat = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
            if (bat == null) {
                return "";
            }
            int level = bat.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
            int scale = bat.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
            int status = bat.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            int temp = bat.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10;
            boolean charging = status == BatteryManager.BATTERY_STATUS_CHARGING
                    || status == BatteryManager.BATTERY_STATUS_FULL;
            int pct = scale > 0 ? (int) (100.0 * level / scale) : -1;
            StringBuilder sb = new StringBuilder();
            if (pct >= 0 && !charging && pct <= 20) {
                sb.append(" · 电量 ").append(pct).append("% 建议插电开服");
            }
            if (temp >= 42) {
                if (sb.length() > 0) {
                    sb.append("；");
                }
                sb.append("机身 ").append(temp).append("℃ 注意散热");
            }
            return sb.toString();
        } catch (Throwable t) {
            return "";
        }
    }

    private String addressText() {        String ips = PythonHost.getIps();
        if (ips.isEmpty()) {
            return "本机 127.0.0.1:" + PythonHost.getPort();
        }
        StringBuilder sb = new StringBuilder();
        for (String ip : ips.split(",")) {
            if (ip.trim().isEmpty()) {
                continue;
            }
            if (sb.length() > 0) {
                sb.append(" · ");
            }
            sb.append("http://").append(ip.trim()).append(":").append(PythonHost.getPort());
        }
        return sb.toString();
    }

    private void stopHost() {
        stopping = true;
        PythonHost.stop();
        if (worker != null) {
            try {
                worker.join(3000);
            } catch (InterruptedException ignored) {
            }
            worker = null;
        }
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.cancel(NOTIF_ID);
        }
    }

    @Override
    public void onDestroy() {
        if (!stopping && !PythonHost.isRunning()) {
            // 系统回收时若还没起来，直接清理
            PythonHost.stop();
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            try {
                wakeLock.release();
            } catch (Exception ignored) {
            }
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null || nm.getNotificationChannel(CHANNEL_ID) != null) {
            return;
        }
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "对局主机",
                NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("手机作为游戏服务器时的常驻通知");
        nm.createNotificationChannel(ch);
    }

    private Notification notification(String title, String text) {
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, flags);

        Intent stop = new Intent(this, HostService.class);
        stop.setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop, flags);

        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            b = new Notification.Builder(this, CHANNEL_ID);
        } else {
            b = new Notification.Builder(this);
        }
        b.setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setSmallIcon(android.R.drawable.ic_menu_compass)
                .setOngoing(true)
                .setContentIntent(pi)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "停止开服", stopPi);
        return b.build();
    }

    private void updateNotification(String title, String text) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(NOTIF_ID, notification(title, text));
        }
    }
}
