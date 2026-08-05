import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hardenPiReasoningProvenanceSource,
  verifyPiReasoningProvenanceSource,
} from "./pi-install-security.ts";

const pinnedSource = `
        else if (event.type === "response.reasoning_summary_text.delta") {
            stream.push({
                type: "thinking_delta",
                contentIndex: slot.contentIndex,
                delta: event.delta,
                partial: output,
            });
        }
        else if (event.type === "response.reasoning_summary_part.done") {
            stream.push({
                type: "thinking_delta",
                contentIndex: slot.contentIndex,
                delta: "\\n\\n",
                partial: output,
            });
        }
        else if (event.type === "response.reasoning_text.delta") {
            stream.push({
                type: "thinking_delta",
                contentIndex: slot.contentIndex,
                delta: event.delta,
                partial: output,
            });
        }
        else if (event.type === "response.output_text.delta") {
`;

test("marks only summary events as safe to expose", () => {
  const hardened = hardenPiReasoningProvenanceSource(pinnedSource);

  verifyPiReasoningProvenanceSource(hardened);
  assert.equal(
    hardened.match(/tertiusReasoningSummary: true/g)?.length,
    2,
  );
  assert.equal(
    hardened.match(/tertiusReasoningSummary: false/g)?.length,
    1,
  );
});

test("reasoning provenance hardening is idempotent", () => {
  const once = hardenPiReasoningProvenanceSource(pinnedSource);
  assert.equal(hardenPiReasoningProvenanceSource(once), once);
});

test("rejects an unexpected pinned source shape", () => {
  assert.throws(
    () => hardenPiReasoningProvenanceSource(
      pinnedSource.replace("response.reasoning_text.delta", "response.reasoning.delta"),
    ),
    /reasoning_text/,
  );
});

test("verification rejects an unmarked install", () => {
  assert.throws(
    () => verifyPiReasoningProvenanceSource(pinnedSource),
    /provenance marker/,
  );
});
