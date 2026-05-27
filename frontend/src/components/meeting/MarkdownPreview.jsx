import { Streamdown } from "streamdown";
import "streamdown/styles.css";

export function MarkdownPreview({ markdown, streaming = false }) {
  return (
    <div className="markdown-preview">
      <Streamdown
        mode={streaming ? "streaming" : "static"}
        parseIncompleteMarkdown
        animated={streaming}
        isAnimating={streaming}
        controls={{ table: true, code: false, mermaid: false }}
      >
        {markdown}
      </Streamdown>
    </div>
  );
}
