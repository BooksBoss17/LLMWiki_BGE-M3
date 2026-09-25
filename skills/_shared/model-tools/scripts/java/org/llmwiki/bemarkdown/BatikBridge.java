package org.llmwiki.bemarkdown;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

import org.apache.batik.transcoder.TranscoderInput;
import org.apache.batik.transcoder.TranscoderOutput;
import org.apache.batik.transcoder.image.PNGTranscoder;
import org.apache.batik.transcoder.wmf.tosvg.WMFTranscoder;

/** Minimal, repository-owned Batik bridge used by the portable runtime. */
public final class BatikBridge {
    private BatikBridge() {}

    private static void requireFile(File file, String label) {
        if (!file.isFile()) {
            throw new IllegalArgumentException(label + " is not a file: " + file);
        }
    }

    private static void ensureParent(File output) {
        File parent = output.getAbsoluteFile().getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IllegalStateException("cannot create output directory: " + parent);
        }
    }

    private static void wmfToSvg(File input, File output) throws Exception {
        WMFTranscoder transcoder = new WMFTranscoder();
        try (InputStream source = new FileInputStream(input);
             OutputStream target = new BufferedOutputStream(new FileOutputStream(output))) {
            transcoder.transcode(new TranscoderInput(source), new TranscoderOutput(target));
        }
    }

    private static void svgToPng(File input, File output, Float width, Float height) throws Exception {
        PNGTranscoder transcoder = new PNGTranscoder();
        if (width != null) {
            transcoder.addTranscodingHint(PNGTranscoder.KEY_WIDTH, width);
        }
        if (height != null) {
            transcoder.addTranscodingHint(PNGTranscoder.KEY_HEIGHT, height);
        }
        TranscoderInput source = new TranscoderInput(input.toURI().toString());
        try (OutputStream target = new BufferedOutputStream(new FileOutputStream(output))) {
            transcoder.transcode(source, new TranscoderOutput(target));
        }
    }

    private static void atomicMove(File temporary, File output) throws Exception {
        java.nio.file.Files.move(
            temporary.toPath(),
            output.toPath(),
            java.nio.file.StandardCopyOption.REPLACE_EXISTING,
            java.nio.file.StandardCopyOption.ATOMIC_MOVE
        );
    }

    private static void wmfToPng(File input, File output, int width, int height) throws Exception {
        requireFile(input, "input");
        ensureParent(output);
        File svg = new File(output.getParentFile(), output.getName() + ".svg");
        File temporarySvg = new File(output.getParentFile(), "." + output.getName() + ".tmp.svg");
        File temporaryPng = new File(output.getParentFile(), "." + output.getName() + ".tmp.png");
        try {
            wmfToSvg(input, temporarySvg);
            atomicMove(temporarySvg, svg);
            svgToPng(svg, temporaryPng, (float) width, (float) height);
            atomicMove(temporaryPng, output);
        } finally {
            temporarySvg.delete();
            temporaryPng.delete();
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length >= 1 && args[0].equals("batch-wmf2png")) {
            if (args.length < 5 || (args.length - 1) % 4 != 0) {
                System.err.println("usage: apache-batik.cmd batch-wmf2png INPUT OUTPUT WIDTH HEIGHT [...]");
                System.exit(2);
            }
            for (int index = 1; index < args.length; index += 4) {
                wmfToPng(
                    new File(args[index]).getAbsoluteFile(),
                    new File(args[index + 1]).getAbsoluteFile(),
                    Integer.parseInt(args[index + 2]),
                    Integer.parseInt(args[index + 3])
                );
            }
            return;
        }
        if (args.length == 5 && args[0].equals("wmf2png")) {
            wmfToPng(
                new File(args[1]).getAbsoluteFile(),
                new File(args[2]).getAbsoluteFile(),
                Integer.parseInt(args[3]),
                Integer.parseInt(args[4])
            );
            return;
        }
        if (args.length != 3 || !(args[0].equals("wmf2svg") || args[0].equals("svg2png"))) {
            System.err.println("usage: apache-batik.cmd wmf2svg|svg2png INPUT OUTPUT | wmf2png INPUT OUTPUT WIDTH HEIGHT | batch-wmf2png ...");
            System.exit(2);
        }
        File input = new File(args[1]).getAbsoluteFile();
        File output = new File(args[2]).getAbsoluteFile();
        requireFile(input, "input");
        ensureParent(output);
        File temporary = new File(output.getParentFile(), "." + output.getName() + ".tmp");
        if (temporary.exists() && !temporary.delete()) {
            throw new IllegalStateException("cannot remove stale temporary output: " + temporary);
        }
        try {
            if (args[0].equals("wmf2svg")) {
                wmfToSvg(input, temporary);
            } else {
                svgToPng(input, temporary, null, null);
            }
            if (!temporary.isFile() || temporary.length() == 0) {
                throw new IllegalStateException("Batik produced an empty output: " + temporary);
            }
            atomicMove(temporary, output);
        } finally {
            if (temporary.exists()) {
                temporary.delete();
            }
        }
    }
}
