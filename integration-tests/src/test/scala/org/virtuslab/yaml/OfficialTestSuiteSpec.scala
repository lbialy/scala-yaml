package org.virtuslab.yaml

/**
 * Runs the vendored yaml-test-suite (integration-tests/yaml-test-suite) and writes
 * integration-tests/target/yaml-suite-results.json.
 *
 * The spec fails when a case listed in loop/ratchet.txt stops passing. Cases that newly pass are
 * printed. `-Dyaml-suite.ratchet=update` adds them to the ratchet file; it never removes an id.
 */
class OfficialTestSuiteSpec extends munit.FunSuite {

  private val repoRoot: os.Path =
    Iterator
      .iterate(os.pwd)(_ / os.up)
      .take(os.pwd.segmentCount + 1)
      .find(p => os.exists(p / "integration-tests" / "yaml-test-suite" / "VERSION"))
      .getOrElse(sys.error(s"integration-tests/yaml-test-suite not found above ${os.pwd}"))

  private val suiteRoot   = repoRoot / "integration-tests" / "yaml-test-suite"
  private val resultsFile = repoRoot / "integration-tests" / "target" / "yaml-suite-results.json"
  private val ratchetFile = repoRoot / "loop" / "ratchet.txt"

  test("yaml-test-suite ratchet") {
    val cases   = YamlSuiteCase.loadAll(suiteRoot)
    val results = YamlSuiteRunner.runAll(cases).sortBy(_.testCase.id)

    os.write.over(resultsFile, YamlSuiteRunner.toJson(results), createFolders = true)

    val passing = results.filter(_.status == YamlSuiteStatus.Pass).map(_.testCase.id).toSet
    val ratchet =
      if (os.exists(ratchetFile)) os.read.lines(ratchetFile).map(_.trim).filter(_.nonEmpty).toSet
      else Set.empty[String]

    val counts = YamlSuiteStatus.all.map(s => s"${s.name}: ${results.count(_.status == s)}")
    println(s"yaml-test-suite: ${results.size} cases, ${counts.mkString(", ")}")
    println(s"results: $resultsFile")

    val unknown = ratchet -- cases.map(_.id)
    assert(
      unknown.isEmpty,
      s"ids in $ratchetFile that are not suite cases: ${sorted(unknown).mkString(" ")}"
    )

    val newlyPassing = passing -- ratchet
    if (newlyPassing.nonEmpty) {
      println(s"newly passing (not in ratchet): ${sorted(newlyPassing).mkString(" ")}")
      if (sys.props.get("yaml-suite.ratchet").contains("update")) {
        os.write.over(
          ratchetFile,
          sorted(ratchet ++ newlyPassing).mkString("", "\n", "\n"),
          createFolders = true
        )
        println(s"added ${newlyPassing.size} ids to $ratchetFile")
      }
    }

    val regressed = results.filter(r => ratchet(r.testCase.id) && r.status != YamlSuiteStatus.Pass)
    val report =
      regressed.map(r => s"  ${r.testCase.id} ${r.status.name}: ${r.detail}").mkString("\n")
    assert(regressed.isEmpty, s"${regressed.size} ratcheted cases stopped passing:\n$report")
  }

  /** LC_ALL=C order: compare UTF-16 code units, which matches bytes for these ASCII ids. */
  private def sorted(ids: Set[String]): List[String] = ids.toList.sorted
}
