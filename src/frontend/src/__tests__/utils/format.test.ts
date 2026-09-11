import {describe, it} from "node:test";
import assert from "node:assert/strict";
import {formatBytes, formatDuration, formatMillis, formatRelativeTime} from "../../utils/format";

describe("formatBytes", () => {
    it("walks the B / KB / MB / GB ladder with the decimals each rung had", () => {
        assert.equal(formatBytes(0), "0 B");
        assert.equal(formatBytes(512), "512 B");
        assert.equal(formatBytes(1536), "1.5 KB");
        assert.equal(formatBytes(12.3 * 1024 * 1024), "12.3 MB");
        assert.equal(formatBytes(1.25 * 1024 * 1024 * 1024), "1.25 GB");
        assert.equal(formatBytes(5 * 1024 ** 4), "5120.00 GB");
    });

    it("renders a missing value as an em dash unless told otherwise", () => {
        assert.equal(formatBytes(null), "—");
        assert.equal(formatBytes(undefined), "—");
        assert.equal(formatBytes(null, {empty: "–"}), "–");
        assert.equal(formatBytes(0, {emptyOnZero: true}), "—");
        assert.equal(formatBytes(0), "0 B");
    });

    it("labels the same 1024 ladder as KiB/MiB/GiB when asked", () => {
        assert.equal(formatBytes(512, {units: "iec"}), "512 B");
        assert.equal(formatBytes(1536, {units: "iec"}), "1.5 KiB");
        assert.equal(formatBytes(3 * 1024 * 1024, {units: "iec"}), "3.0 MiB");
        assert.equal(formatBytes(2 * 1024 ** 3, {units: "iec"}), "2.00 GiB");
    });

    it("can stop at MB, so a source over a gigabyte reads as a big MB count", () => {
        assert.equal(formatBytes(1536, {maxUnit: "MB"}), "1.5 KB");
        assert.equal(formatBytes(1.5 * 1024 ** 3, {maxUnit: "MB"}), "1536.0 MB");
    });

    it("compact: one decimal under 10, none above, and a TB rung", () => {
        assert.equal(formatBytes(512, {compact: true}), "512 B");
        assert.equal(formatBytes(1536, {compact: true}), "1.5 KB");
        assert.equal(formatBytes(15.3 * 1024 * 1024, {compact: true}), "15 MB");
        assert.equal(formatBytes(1.5 * 1024 ** 3, {compact: true}), "1.5 GB");
        assert.equal(formatBytes(3 * 1024 ** 4, {compact: true}), "3.0 TB");
        assert.equal(formatBytes(3000 * 1024 ** 4, {compact: true}), "3000 TB");
    });
});

describe("formatDuration", () => {
    it("rounds to one coarse unit", () => {
        assert.equal(formatDuration(0), "0s");
        assert.equal(formatDuration(42.4), "42s");
        assert.equal(formatDuration(59.6), "60s");
        assert.equal(formatDuration(150), "3m");
        assert.equal(formatDuration(3600 * 2.4), "2h");
        assert.equal(formatDuration(86400 * 5), "5d");
    });

    it("keeps the sign on a negative input unless a placeholder is given", () => {
        assert.equal(formatDuration(-5), "-5s");
        assert.equal(formatDuration(-5, {negative: "—"}), "—");
        assert.equal(formatDuration(5, {negative: "—"}), "5s");
    });
});

describe("formatMillis", () => {
    it("ms under a second, one-decimal seconds above", () => {
        assert.equal(formatMillis(0), "0 ms");
        assert.equal(formatMillis(999), "999 ms");
        assert.equal(formatMillis(1000), "1.0 s");
        assert.equal(formatMillis(1234), "1.2 s");
        assert.equal(formatMillis(125_000), "125.0 s");
    });

    it("precise: two decimals under a minute, minutes and seconds above", () => {
        assert.equal(formatMillis(999, {precise: true}), "999 ms");
        assert.equal(formatMillis(1234, {precise: true}), "1.23 s");
        assert.equal(formatMillis(125_000, {precise: true}), "2m 5.0s");
        assert.equal(formatMillis(3_601_500, {precise: true}), "60m 1.5s");
    });

    it("renders a missing value as an em dash unless told otherwise", () => {
        assert.equal(formatMillis(null), "—");
        assert.equal(formatMillis(undefined), "—");
        assert.equal(formatMillis(null, {empty: "–", precise: true}), "–");
    });
});

describe("formatRelativeTime", () => {
    const now = 1_700_000_000;

    it("says how long ago in one coarse unit", () => {
        assert.equal(formatRelativeTime(now, now), "0s ago");
        assert.equal(formatRelativeTime(now - 42, now), "42s ago");
        assert.equal(formatRelativeTime(now - 150, now), "3m ago");
        assert.equal(formatRelativeTime(now - 3600 * 5, now), "5h ago");
        assert.equal(formatRelativeTime(now - 86400 * 9, now), "9d ago");
    });

    it("flags a timestamp ahead of now instead of rendering a negative age", () => {
        assert.equal(formatRelativeTime(now + 1, now), "in the future");
    });
});
