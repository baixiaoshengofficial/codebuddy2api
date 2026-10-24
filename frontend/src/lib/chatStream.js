/** Parse SSE framing independently from fetch, UI state, or authentication. */
export function createSseParser(onEvent) {
  let buffer = "";
  function dispatch(frame) {
    const lines = frame.split(/\r\n|\r|\n/);
    const data = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (data) onEvent(data);
  }
  return {
    push(text) {
      buffer += text;
      let match;
      while ((match = /\r\n\r\n|\n\n|\r\r/.exec(buffer))) {
        dispatch(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
    },
    finish() {
      if (buffer.trim()) dispatch(buffer);
      buffer = "";
    },
  };
}

export async function consumeChatStream(body, onChunk) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let complete = false;
  const parser = createSseParser((data) => {
    if (data === "[DONE]") {
      complete = true;
      return;
    }
    const value = JSON.parse(data);
    if (value.error)
      throw new Error(value.error.message || String(value.error));
    onChunk(value);
  });
  try {
    while (!complete) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.finish();
    if (!complete)
      throw new Error("响应流中断，未收到结束标记；请到请求明细查看结果");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
