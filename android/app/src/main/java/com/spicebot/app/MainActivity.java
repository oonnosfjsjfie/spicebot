package com.spicebot.app;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
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

import java.io.InputStream;
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
        settings.setUserAgentString(settings.getUserAgentString() + " SpiceBotApp/2");

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
