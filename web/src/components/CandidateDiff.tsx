type CandidateDiffProps = {
  diff: string;
};

export function CandidateDiff({ diff }: CandidateDiffProps) {
  return (
    <section className="candidate-diff" aria-label={"\u5019\u9009\u7248\u672c\u5dee\u5f02"}>
      <div className="candidate-diff-heading">
        <span>{"\u4e0d\u53ef\u53d8\u5019\u9009\u5dee\u5f02 / DIFF"}</span>
        <small>{"\u53ea\u8bfb"}</small>
      </div>
      <pre>{diff}</pre>
    </section>
  );
}
