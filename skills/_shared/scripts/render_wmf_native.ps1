param(
    [string]$Task = "",

    [string]$Source,

    [string]$Output,

    [int]$Width = 0,

    [int]$Height = 0
)

$ErrorActionPreference = 'Stop'

function Write-WmfTrace {
    param([string]$Message)
    if ($env:LLMWIKI_WMF_TRACE_FILE) {
        [System.IO.File]::AppendAllText(
            $env:LLMWIKI_WMF_TRACE_FILE,
            $Message + [System.Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

Write-WmfTrace 'PowerShell adapter start'
Add-Type -AssemblyName System.Drawing
Write-WmfTrace 'System.Drawing loaded'

# Graphics.DrawImage(Metafile) can hang on some MathType WMFs. Classic GDI
# plays the records into the final bitmap in one pass while retaining bounds.
$runtimeRoot = if ($env:LLMWIKI_MODEL_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:LLMWIKI_MODEL_RUNTIME_ROOT)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\model-tools\runtime'))
}
$nativeAssembly = Join-Path $runtimeRoot 'tools\wmf-native-adapter\v1.0.0\LLMWikiClassicWmfRenderer.v1.dll'
if (Test-Path -LiteralPath $nativeAssembly) {
    Write-WmfTrace ("loading native assembly path=" + $nativeAssembly)
    Add-Type -Path $nativeAssembly
    Write-WmfTrace 'precompiled native assembly loaded'
}
else {
    Add-Type -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

public static class LLMWikiClassicWmfRenderer
{
    [StructLayout(LayoutKind.Sequential)]
    private struct BITMAPINFOHEADER
    {
        public uint biSize;
        public int biWidth;
        public int biHeight;
        public ushort biPlanes;
        public ushort biBitCount;
        public uint biCompression;
        public uint biSizeImage;
        public int biXPelsPerMeter;
        public int biYPelsPerMeter;
        public uint biClrUsed;
        public uint biClrImportant;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BITMAPINFO
    {
        public BITMAPINFOHEADER bmiHeader;
        public uint bmiColors;
    }

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr SetMetaFileBitsEx(uint size, byte[] data);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteMetaFile(IntPtr metafile);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PlayMetaFile(IntPtr hdc, IntPtr metafile);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr CreateCompatibleDC(IntPtr hdc);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteDC(IntPtr hdc);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr CreateDIBSection(
        IntPtr hdc,
        ref BITMAPINFO info,
        uint usage,
        out IntPtr bits,
        IntPtr section,
        uint offset
    );

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteObject(IntPtr obj);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PatBlt(IntPtr hdc, int x, int y, int width, int height, uint operation);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern int SetMapMode(IntPtr hdc, int mode);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowOrgEx(IntPtr hdc, int x, int y, IntPtr oldPoint);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowExtEx(IntPtr hdc, int x, int y, IntPtr oldSize);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetViewportOrgEx(IntPtr hdc, int x, int y, IntPtr oldPoint);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetViewportExtEx(IntPtr hdc, int x, int y, IntPtr oldSize);

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumMetaFile(
        IntPtr hdc,
        IntPtr metafile,
        EnumMetaFileProc callback,
        IntPtr callbackData
    );

    [DllImport("gdi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PlayMetaFileRecord(
        IntPtr hdc,
        IntPtr handleTable,
        IntPtr metaRecord,
        uint objectCount
    );

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private delegate bool EnumMetaFileProc(
        IntPtr hdc,
        IntPtr handleTable,
        IntPtr metaRecord,
        int objectCount,
        IntPtr callbackData
    );

    private static void Require(bool ok, string operation)
    {
        if (!ok)
            throw new System.ComponentModel.Win32Exception(
                Marshal.GetLastWin32Error(),
                operation + " failed"
            );
    }

    private static bool IsMathTypeAnnotation(IntPtr metaRecord)
    {
        // META_ESCAPE records can contain a private AppsMFCC/MathTypeUU copy
        // of the equation.  It is metadata, not drawing content, and passing
        // it to GDI is what hangs on the real MathType regression fixture.
        int sizeWords = Marshal.ReadInt32(metaRecord, 0);
        ushort function = unchecked((ushort)Marshal.ReadInt16(metaRecord, 4));
        if (function != 0x0626 || sizeWords < 3 || sizeWords > 8 * 1024 * 1024)
            return false;
        byte[] bytes = new byte[checked(sizeWords * 2)];
        Marshal.Copy(metaRecord, bytes, 0, bytes.Length);
        ushort escapeFunction = BitConverter.ToUInt16(bytes, 6);
        if (escapeFunction == 15) // MFCOMMENT is metadata, never a drawing record.
            return true;
        string payload = System.Text.Encoding.ASCII.GetString(bytes);
        return payload.Contains("AppsMFCC") || payload.Contains("MathTypeUU");
    }

    private static int CountRecordsBeforeEof(byte[] file)
    {
        int offset = 22 + 18;
        int count = 0;
        while (offset + 6 <= file.Length)
        {
            int sizeWords = BitConverter.ToInt32(file, offset);
            ushort function = BitConverter.ToUInt16(file, offset + 4);
            if (sizeWords < 3 || offset + checked(sizeWords * 2) > file.Length)
                throw new InvalidDataException("The WMF record table is truncated or invalid");
            if (function == 0)
                return count;
            count++;
            offset += checked(sizeWords * 2);
        }
        throw new InvalidDataException("The WMF record table has no META_EOF record");
    }

    private static byte[] FilterNonDrawingComments(byte[] file, out int skipped)
    {
        if (file.Length < 46)
            throw new InvalidDataException("The WMF is too short");
        skipped = 0;
        int maxRecordWords = 3;
        bool sawEof = false;
        using (MemoryStream output = new MemoryStream(file.Length))
        {
            output.Write(file, 0, 40); // Placeable header plus METAHEADER.
            int offset = 40;
            while (offset + 6 <= file.Length)
            {
                int sizeWords = BitConverter.ToInt32(file, offset);
                ushort function = BitConverter.ToUInt16(file, offset + 4);
                int sizeBytes = checked(sizeWords * 2);
                if (sizeWords < 3 || offset + sizeBytes > file.Length)
                    throw new InvalidDataException("The WMF record table is truncated or invalid");
                bool isNonDrawingComment =
                    function == 0x0626
                    && sizeWords >= 5
                    && BitConverter.ToUInt16(file, offset + 6) == 15;
                if (isNonDrawingComment)
                {
                    skipped++;
                }
                else
                {
                    output.Write(file, offset, sizeBytes);
                    maxRecordWords = Math.Max(maxRecordWords, sizeWords);
                }
                offset += sizeBytes;
                if (function == 0)
                {
                    sawEof = true;
                    break;
                }
            }
            if (!sawEof)
                throw new InvalidDataException("The WMF record table has no META_EOF record");
            byte[] filtered = output.ToArray();
            int metafileWords = checked((filtered.Length - 22) / 2);
            Buffer.BlockCopy(BitConverter.GetBytes(metafileWords), 0, filtered, 28, 4);
            Buffer.BlockCopy(BitConverter.GetBytes(maxRecordWords), 0, filtered, 34, 4);
            return filtered;
        }
    }

    private static int PlayFilteredRecords(IntPtr dc, IntPtr metafile, int expectedRecords)
    {
        int skipped = 0;
        int seen = 0;
        Exception callbackError = null;
        EnumMetaFileProc callback = delegate(
            IntPtr callbackDc,
            IntPtr handleTable,
            IntPtr metaRecord,
            int objectCount,
            IntPtr callbackData
        ) {
            try
            {
                seen++;
                ushort function = unchecked((ushort)Marshal.ReadInt16(metaRecord, 4));
                bool trace = Environment.GetEnvironmentVariable("LLMWIKI_WMF_TRACE") == "1";
                string traceFile = Environment.GetEnvironmentVariable("LLMWIKI_WMF_TRACE_FILE");
                Action<string> log = delegate(string value) {
                    if (trace)
                        Console.Error.WriteLine(value);
                    if (!String.IsNullOrEmpty(traceFile))
                        File.AppendAllText(traceFile, value + Environment.NewLine);
                };
                if (trace)
                    log("WMF record start function=0x" + function.ToString("X4"));
                if (IsMathTypeAnnotation(metaRecord))
                {
                    skipped++;
                    log("WMF record skipped function=0x" + function.ToString("X4"));
                    return true;
                }
                if (!PlayMetaFileRecord(callbackDc, handleTable, metaRecord, unchecked((uint)objectCount)))
                    throw new System.ComponentModel.Win32Exception(
                        Marshal.GetLastWin32Error(),
                        "PlayMetaFileRecord failed"
                    );
                log("WMF record end function=0x" + function.ToString("X4"));
                return true;
            }
            catch (Exception ex)
            {
                callbackError = ex;
                return false;
            }
        };
        bool enumerated = EnumMetaFile(dc, metafile, callback, IntPtr.Zero);
        GC.KeepAlive(callback);
        if (callbackError != null)
            throw new InvalidOperationException("WMF record playback failed", callbackError);
        if (seen != expectedRecords)
            throw new InvalidOperationException(
                "WMF enumeration stopped early: expected=" + expectedRecords + " seen=" + seen
            );
        if (!enumerated)
            throw new System.ComponentModel.Win32Exception(
                Marshal.GetLastWin32Error(),
                "EnumMetaFile failed"
            );
        if (seen < expectedRecords)
            throw new InvalidOperationException("WMF enumeration ended before all records were played");
        return skipped;
    }

    public static int Render(string input, string output, int width, int height)
    {
        string startupTrace = Environment.GetEnvironmentVariable("LLMWIKI_WMF_TRACE_FILE");
        if (!String.IsNullOrEmpty(startupTrace))
            File.AppendAllText(startupTrace, "WMF render start" + Environment.NewLine);
        byte[] file = File.ReadAllBytes(input);
        if (file.Length <= 22 || BitConverter.ToUInt32(file, 0) != 0x9AC6CDD7)
            throw new InvalidDataException("The input is not a placeable WMF");

        int left = BitConverter.ToInt16(file, 6);
        int top = BitConverter.ToInt16(file, 8);
        int right = BitConverter.ToInt16(file, 10);
        int bottom = BitConverter.ToInt16(file, 12);
        int sourceWidth = Math.Abs(right - left);
        int sourceHeight = Math.Abs(bottom - top);
        if (sourceWidth < 1 || sourceHeight < 1)
            throw new InvalidDataException("The placeable WMF has invalid bounds");
        int skippedAnnotations;
        byte[] filtered = FilterNonDrawingComments(file, out skippedAnnotations);
        int expectedRecords = CountRecordsBeforeEof(filtered);
        byte[] records = new byte[filtered.Length - 22];
        Buffer.BlockCopy(filtered, 22, records, 0, records.Length);
        IntPtr metafile = SetMetaFileBitsEx((uint)records.Length, records);
        if (metafile == IntPtr.Zero)
            throw new System.ComponentModel.Win32Exception(
                Marshal.GetLastWin32Error(),
                "SetMetaFileBitsEx failed"
            );

        IntPtr dc = IntPtr.Zero;
        IntPtr dib = IntPtr.Zero;
        IntPtr previous = IntPtr.Zero;
        try
        {
            dc = CreateCompatibleDC(IntPtr.Zero);
            if (dc == IntPtr.Zero)
                throw new System.ComponentModel.Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "CreateCompatibleDC failed"
                );
            BITMAPINFO info = new BITMAPINFO();
            info.bmiHeader.biSize = (uint)Marshal.SizeOf(typeof(BITMAPINFOHEADER));
            info.bmiHeader.biWidth = width;
            info.bmiHeader.biHeight = -height;
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            IntPtr bits;
            dib = CreateDIBSection(dc, ref info, 0, out bits, IntPtr.Zero, 0);
            if (dib == IntPtr.Zero)
                throw new System.ComponentModel.Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "CreateDIBSection failed"
                );
            previous = SelectObject(dc, dib);
            if (previous == IntPtr.Zero || previous == new IntPtr(-1))
                throw new System.ComponentModel.Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "SelectObject failed"
                );
            Require(PatBlt(dc, 0, 0, width, height, 0x00FF0062), "PatBlt");
            if (SetMapMode(dc, 8) == 0)
                throw new System.ComponentModel.Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "SetMapMode failed"
                );
            Require(SetWindowOrgEx(dc, left, top, IntPtr.Zero), "SetWindowOrgEx");
            Require(SetWindowExtEx(dc, sourceWidth, sourceHeight, IntPtr.Zero), "SetWindowExtEx");
            Require(SetViewportOrgEx(dc, 0, 0, IntPtr.Zero), "SetViewportOrgEx");
            Require(SetViewportExtEx(dc, width, height, IntPtr.Zero), "SetViewportExtEx");
            int skippedDuringPlayback = PlayFilteredRecords(dc, metafile, expectedRecords);

            byte[] pixels = new byte[checked(width * height * 4)];
            Marshal.Copy(bits, pixels, 0, pixels.Length);
            for (int index = 3; index < pixels.Length; index += 4)
                pixels[index] = 255;
            Marshal.Copy(pixels, 0, bits, pixels.Length);
            using (Bitmap bitmap = Image.FromHbitmap(dib))
                bitmap.Save(output, ImageFormat.Png);
            return skippedAnnotations + skippedDuringPlayback;
        }
        finally
        {
            if (previous != IntPtr.Zero && dc != IntPtr.Zero)
                SelectObject(dc, previous);
            if (dib != IntPtr.Zero)
                DeleteObject(dib);
            if (dc != IntPtr.Zero)
                DeleteDC(dc);
            DeleteMetaFile(metafile);
        }
    }
}
'@ -ReferencedAssemblies System.Drawing
}

function Render-OneWmf {
    param([string]$InputPath, [string]$OutputPath, [int]$TargetWidth, [int]$TargetHeight)
    if ($TargetWidth -lt 1 -or $TargetWidth -gt 32768 -or $TargetHeight -lt 1 -or $TargetHeight -gt 32768) {
        throw "Invalid native WMF render dimensions: ${TargetWidth}x${TargetHeight}"
    }
    $sourcePath = [System.IO.Path]::GetFullPath($InputPath)
    $destinationPath = [System.IO.Path]::GetFullPath($OutputPath)
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($destinationPath)) | Out-Null
    Write-WmfTrace ("PowerShell render invoke source=" + $sourcePath)
    $skipped = [LLMWikiClassicWmfRenderer]::Render($sourcePath, $destinationPath, $TargetWidth, $TargetHeight)
    return [ordered]@{ ok = $true; output = $destinationPath; width = $TargetWidth; height = $TargetHeight; skipped_annotations = $skipped }
}

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($Task) {
    $jobs = Get-Content -LiteralPath $Task -Raw -Encoding UTF8 | ConvertFrom-Json
    $results = @()
    foreach ($job in $jobs) {
        $results += Render-OneWmf -InputPath $job.source -OutputPath $job.output -TargetWidth $job.width -TargetHeight $job.height
    }
    Write-Output ($results | ConvertTo-Json -Compress -Depth 4)
}
else {
    if (-not $Source -or -not $Output) { throw "Source and Output are required when Task is omitted" }
    $result = Render-OneWmf -InputPath $Source -OutputPath $Output -TargetWidth $Width -TargetHeight $Height
    Write-Output ($result | ConvertTo-Json -Compress)
}
