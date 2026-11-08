package com.spicebot.app;

import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.widget.Toast;
import android.os.Handler;
import android.os.Looper;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.HashMap;

/**
 * Like the Neutrix app, SpiceBot opens the live website (on Render), so every change to the website
 * reaches the app without a new install. The website keeps a copy of itself on the phone for offline use. If the
 * phone is offline and no copy is saved yet (the very first launch), the app opens the copy built into
 * it instead, served from https://appassets.androidplatform.net/assets/. Outside links open in the browser.
 */
public class MainActivity extends Activity {
    static final String HOST = "appassets.androidplatform.net";
    static final String LOCAL = "https://" + HOST + "/assets/index.html";
    static final String SITE_HOST = "spicebot.onrender.com";
    static final String LIVE = "https://" + SITE_HOST + "/";
    static final long SLOW_MS = 12000;
    static final int FILE_REQUEST = 42;

    WebView webView;
    ValueCallback<Uri[]> fileCallback;
    boolean usingLocal;
    boolean liveLoaded;
    final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setUserAgentString(settings.getUserAgentString() + " SpiceBotApp/3");
        // Lets the website save files it makes (PDF reports) to the phone.
        webView.addJavascriptInterface(new Bridge(this), "SpiceBotAndroid");

        webView.setWebViewClient(new AppClient(this));
        webView.setWebChromeClient(new FileClient(this));

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(LIVE);
            // The free server sleeps when nobody uses it. If it is slow to wake, show the built-in copy;
            // its chat still reaches SpiceBot's AI once the server is awake.
            handler.postDelayed(new SlowCheck(this), SLOW_MS);
        }
    }

    static String mimeFor(String path) {
        String p = path.toLowerCase();
        if (p.endsWith(".html")) return "text/html";
        if (p.endsWith(".js")) return "text/javascript";
        if (p.endsWith(".css")) return "text/css";
        if (p.endsWith(".json")) return "application/json";
        if (p.endsWith(".png")) return "image/png";
        if (p.endsWith(".svg")) return "image/svg+xml";
        if (p.endsWith(".txt") || p.endsWith(".md")) return "text/plain";
        return "application/octet-stream";
    }

    WebResourceResponse serveAsset(Uri url) {
        if (!HOST.equals(url.getHost())) return null;
        String path = url.getPath();
        if (path == null || !path.startsWith("/assets/")) return notFound();
        String name = path.substring("/assets/".length());
        try {
            InputStream in = getAssets().open(name);
            String mime = mimeFor(name);
            String enc = mime.startsWith("text/") || mime.endsWith("json") || mime.endsWith("xml") ? "UTF-8" : null;
            return new WebResourceResponse(mime, enc, in);
        } catch (Exception e) {
            return notFound();
        }
    }

    static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", new HashMap<String, String>(), null);
    }

    static boolean isOurs(Uri url) {
        String host = url.getHost();
        if (HOST.equals(host)) return true;
        String path = url.getPath();
        return SITE_HOST.equals(host) && (path == null || !path.endsWith(".apk"));
    }

    void useLocalCopy(WebView view) {
        if (usingLocal) return;
        usingLocal = true;
        view.loadUrl(LOCAL);
    }

    void openOutside(Uri url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, url));
        } catch (Exception ignored) {
            // No app can open this link; stay on SpiceBot.
        }
    }

    void pickFiles(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params) throws Exception {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        fileCallback = callback;
        startActivityForResult(params.createIntent(), FILE_REQUEST);
    }

    /** Saves a file to Downloads/SpiceBot (Android 10+) and opens it; on older phones, offers the share sheet. */
    void saveFile(String base64, String name, String mime) {
        try {
            byte[] data = Base64.decode(base64, Base64.DEFAULT);
            if (data.length > 15000000) throw new Exception("too large");
            String safe = name.replaceAll("[^A-Za-z0-9._ -]", "_");
            if (safe.length() == 0 || safe.length() > 80) safe = "SpiceBot report.pdf";
            if (!"application/pdf".equals(mime)) mime = "application/octet-stream";
            Uri uri;
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.MediaColumns.DISPLAY_NAME, safe);
                v.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                v.put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/SpiceBot");
                uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                OutputStream out = getContentResolver().openOutputStream(uri);
                out.write(data);
                out.close();
                Toast.makeText(this, "Saved to Downloads/SpiceBot: " + safe, Toast.LENGTH_LONG).show();
                Intent open = new Intent(Intent.ACTION_VIEW);
                open.setDataAndType(uri, mime);
                open.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                try { startActivity(open); } catch (Exception ignored) { /* no PDF viewer; it's still saved */ }
            } else {
                File dir = new File(getCacheDir(), "reports");
                dir.mkdirs();
                File f = new File(dir, safe);
                FileOutputStream out = new FileOutputStream(f);
                out.write(data);
                out.close();
                uri = Uri.parse("content://" + FileShare.AUTHORITY + "/" + Uri.encode(safe));
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType(mime);
                send.putExtra(Intent.EXTRA_STREAM, uri);
                send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                startActivity(Intent.createChooser(send, "Save or share " + safe));
            }
        } catch (Exception e) {
            Toast.makeText(this, "Couldn't save the file: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    static class Bridge {
        final MainActivity a;
        Bridge(MainActivity a) { this.a = a; }

        @JavascriptInterface
        public void saveFile(String base64, String name, String mime) {
            a.runOnUiThread(new SaveTask(a, base64, name, mime));
        }

        @JavascriptInterface
        public boolean canSave() { return true; }
    }

    static class SaveTask implements Runnable {
        final MainActivity a; final String b, n, m;
        SaveTask(MainActivity a, String b, String n, String m) { this.a = a; this.b = b; this.n = n; this.m = m; }

        @Override
        public void run() { a.saveFile(b, n, m); }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    static class AppClient extends WebViewClient {
        final MainActivity a;
        AppClient(MainActivity a) { this.a = a; }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            return a.serveAsset(request.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            if (isOurs(url)) return false;
            a.openOutside(url);
            return true;
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            if (url != null && url.contains(SITE_HOST)) a.liveLoaded = true;
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            // Offline with nothing saved yet: open the copy built into the app.
            if (request.isForMainFrame() && SITE_HOST.equals(request.getUrl().getHost())) a.useLocalCopy(view);
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (request.isForMainFrame() && SITE_HOST.equals(request.getUrl().getHost()) && response.getStatusCode() >= 400) a.useLocalCopy(view);
        }
    }

    static class SlowCheck implements Runnable {
        final MainActivity a;
        SlowCheck(MainActivity a) { this.a = a; }

        @Override
        public void run() {
            if (!a.liveLoaded) a.useLocalCopy(a.webView);
        }
    }

    static class FileClient extends WebChromeClient {
        final MainActivity a;
        FileClient(MainActivity a) { this.a = a; }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            try {
                a.pickFiles(callback, params);
                return true;
            } catch (Exception e) {
                a.fileCallback = null;
                return false;
            }
        }
    }
}
