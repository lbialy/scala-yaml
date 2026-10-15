### Test suite

`yaml-test-suite/` is the [yaml-test-suite](https://github.com/yaml/yaml-test-suite) data release named in `yaml-test-suite/VERSION`, vendored by `fetch-yaml-test-suite.sh`. It sits outside `src/test/resources` so sbt does not copy it onto the classpath. The release's `tags/` symlink tree is flattened into `yaml-test-suite/tags.txt` (`<id> <tag> ...`).

```bash
sbt 'integration/testOnly *OfficialTestSuiteSpec'
```

Every case, subcases like `2G84/00` included, gets one status:

| Status | Meaning |
|---|---|
| `pass` | events equal `test.event`, or the case has an `error` file and the parser rejected it |
| `events-differ` | parsed, events differ from `test.event` |
| `wrongly-rejected` | no `error` file, parser returned an error |
| `wrongly-accepted` | `error` file, parser produced the whole stream |
| `crash` | an exception other than `YamlError`, or a timeout |

Results go to `integration-tests/target/yaml-suite-results.json` (`id`, `tags`, `status`, `detail`: the first differing event or the error).

`loop/ratchet.txt` lists the passing ids, sorted with `LC_ALL=C`. The spec fails when any of them stops passing. Newly passing ids are printed; `sbt -Dyaml-suite.ratchet=update 'integration/testOnly *OfficialTestSuiteSpec'` adds them. It never removes an id.

Events are rendered by `TestRunnerUtils.convertEventToYamlTestSuiteFormat`, in `test.event` form: values escape `\` `\b` `\n` `\r` `\t`, and flow collections print `+SEQ []` / `+MAP {}` from the `flow` flag on `SequenceStart` / `MappingStart`.
