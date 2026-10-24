import test from "node:test";
import assert from "node:assert/strict";
import { createSseParser, consumeChatStream } from "../src/lib/chatStream.js";

const body = (chunks) =>
  new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) =>
        controller.enqueue(new TextEncoder().encode(chunk)),
      );
      controller.close();
    },
  });
test("SSE preserves split CRLF frames, comments, and final buffered events", () => {
  const events = [],
    parser = createSseParser((value) => events.push(value));
  parser.push(': keep alive\r\ndata:{"text":"你');
  parser.push('好"}\r\n\r');
  parser.push("\ndata: [DONE]");
  parser.finish();
  assert.deepEqual(events, ['{"text":"你好"}', "[DONE]"]);
});
test("SSE supports multiline data", () => {
  const values = [],
    parser = createSseParser((value) => values.push(JSON.parse(value)));
  parser.push('data: {"a":\ndata: 1}\n\n');
  assert.deepEqual(values, [{ a: 1 }]);
});
test("HTTP 200 SSE error is a failed call and keeps preceding output", async () => {
  const values = [];
  await assert.rejects(
    consumeChatStream(
      body([
        'data: {"choices":[]}\n\n',
        'data: {"error":{"message":"积分不足"}}\n\n',
      ]),
      (value) => values.push(value),
    ),
    /积分不足/,
  );
  assert.equal(values.length, 1);
});
test("unexpected EOF is never reported as success", async () => {
  await assert.rejects(
    consumeChatStream(body(['data: {"choices":[]}\n\n']), () => {}),
    /响应流中断/,
  );
});
test("complete stream resolves and malformed JSON rejects", async () => {
  await consumeChatStream(
    body(['data: {"choices":[]}\n\ndata: [DONE]\n\n']),
    () => {},
  );
  await assert.rejects(
    consumeChatStream(body(["data: invalid\n\n"]), () => {}),
    SyntaxError,
  );
});

test("UTF-8 characters survive a split between every byte", async () => {
  const bytes = new TextEncoder().encode(
    'data: {"text":"你好"}\n\ndata: [DONE]\n\n',
  );
  const body = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
  const values = [];
  await consumeChatStream(body, (value) => values.push(value));
  assert.equal(values[0].text, "你好");
});
