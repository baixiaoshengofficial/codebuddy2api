/** Examples contain placeholders only; never embed the stored access password. */
export function requestExample(origin, body, language) {
  if (language === "python")
    return `from openai import OpenAI

client = OpenAI(
    api_key="YOUR_PASSWORD",
    base_url="${origin}/codebuddy/v1",
    default_headers={"X-Client-Name": "My Client"},
)
response = client.chat.completions.create(
    model=${JSON.stringify(body.model)},
    messages=[{"role": "user", "content": ${JSON.stringify(body.messages[0].content)}}],
    stream=${body.stream ? "True" : "False"},
)
${body.stream ? 'for chunk in response:\n    print(chunk.choices[0].delta.content or "", end="")' : "print(response.choices[0].message.content)"}`;
  return `curl '${origin}/codebuddy/v1/chat/completions' \\
  -H 'Authorization: Bearer YOUR_PASSWORD' \\
  -H 'Content-Type: application/json' \\
  -H 'X-Client-Name: My Client' \\
  -d '${JSON.stringify(body).replace(/'/g, `'"'"'`)}'`;
}
