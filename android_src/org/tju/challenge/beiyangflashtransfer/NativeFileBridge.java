package org.tju.challenge.beiyangflashtransfer;

import android.app.Activity;
import android.content.ClipData;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

public final class NativeFileBridge {
    private static final int BUFFER_SIZE = 64 * 1024;

    private NativeFileBridge() {
    }

    public static Intent createOpenDocumentIntent() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, false);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        return intent;
    }

    public static String copyResultToCache(Activity activity, Intent data, String folderName) throws Exception {
        if (activity == null) {
            throw new IllegalArgumentException("Activity is null");
        }
        if (data == null) {
            throw new IllegalArgumentException("No file was returned by Android");
        }

        Uri uri = extractUri(data);
        if (uri == null) {
            throw new IllegalArgumentException("Android did not return a readable file URI");
        }
        return copyUriToCache(activity, uri, folderName);
    }

    public static String copyUriToCache(Activity activity, String uriText, String folderName) throws Exception {
        if (uriText == null || uriText.trim().isEmpty()) {
            throw new IllegalArgumentException("Android did not return a readable file URI");
        }
        return copyUriToCache(activity, Uri.parse(uriText), folderName);
    }

    private static String copyUriToCache(Activity activity, Uri uri, String folderName) throws Exception {
        ContentResolver resolver = activity.getContentResolver();
        try {
            resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (Exception ignored) {
        }

        String fileName = queryDisplayName(resolver, uri);
        File targetDir = new File(activity.getCacheDir(), sanitizeFileName(folderName));
        if (!targetDir.exists() && !targetDir.mkdirs()) {
            throw new IllegalStateException("Could not create cache folder: " + targetDir.getAbsolutePath());
        }
        File target = uniqueFile(targetDir, sanitizeFileName(fileName));

        try (InputStream input = resolver.openInputStream(uri);
             FileOutputStream output = new FileOutputStream(target)) {
            if (input == null) {
                throw new IllegalStateException("Could not open the selected file");
            }
            byte[] buffer = new byte[BUFFER_SIZE];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.flush();
            output.getFD().sync();
        }

        if (!target.isFile() || target.length() <= 0) {
            throw new IllegalStateException("Selected file is empty or could not be copied");
        }
        return target.getAbsolutePath();
    }

    private static Uri extractUri(Intent data) {
        Uri uri = data.getData();
        if (uri != null) {
            return uri;
        }
        ClipData clipData = data.getClipData();
        if (clipData == null || clipData.getItemCount() <= 0) {
            return null;
        }
        ClipData.Item item = clipData.getItemAt(0);
        return item == null ? null : item.getUri();
    }

    private static String queryDisplayName(ContentResolver resolver, Uri uri) {
        String name = null;
        try (Cursor cursor = resolver.query(uri, null, null, null, null)) {
            if (cursor != null) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0 && cursor.moveToFirst()) {
                    name = cursor.getString(index);
                }
            }
        } catch (Exception ignored) {
        }
        if (name == null || name.trim().isEmpty()) {
            name = "selected_file";
        }
        return name;
    }

    private static String sanitizeFileName(String name) {
        String value = name == null ? "" : name.trim();
        if (value.isEmpty() || ".".equals(value) || "..".equals(value)) {
            value = "selected_file";
        }
        StringBuilder out = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (ch < 32 || ch == '/' || ch == '\\' || ch == ':' || ch == '*' ||
                    ch == '?' || ch == '"' || ch == '<' || ch == '>' || ch == '|') {
                out.append('_');
            } else {
                out.append(ch);
            }
        }
        return out.length() == 0 ? "selected_file" : out.toString();
    }

    private static File uniqueFile(File dir, String fileName) {
        File target = new File(dir, fileName);
        if (!target.exists()) {
            return target;
        }
        String base = fileName;
        String ext = "";
        int dot = fileName.lastIndexOf('.');
        if (dot > 0 && dot < fileName.length() - 1) {
            base = fileName.substring(0, dot);
            ext = fileName.substring(dot);
        }
        for (int i = 1; i < 10000; i++) {
            target = new File(dir, base + "_" + i + ext);
            if (!target.exists()) {
                return target;
            }
        }
        return new File(dir, base + "_" + System.currentTimeMillis() + ext);
    }
}
