package org.virtuslab.yaml.benchmark

import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.security.MessageDigest

import scala.util.Using

import org.virtuslab.yaml.TestRunnerUtils

class CorpusBenchSuite extends munit.FunSuite {

  private val files = List("cgmes-core.yml", "cgmes-dynamics.yml", "TC57CIM.yml")

  // A human regenerates digests when an intended event-format change lands.
  // A mismatch is a review signal. The agent loop must not pass -Dregenerate=true.
  private val regenerate = sys.props.get("regenerate").contains("true")

  for (file <- files) {
    test(s"$file from string to events") {
      assertEquals(bench(file).fromStringToEvents.isRight, true)
    }

    test(s"$file from string to node") {
      assertEquals(bench(file).fromStringToNode.isRight, true)
    }

    test(s"$file event digest") {
      val first  = eventDigest(file)
      val second = eventDigest(file)
      assertEquals(first, second, "event digest changed across two parses")

      val expected =
        if (regenerate) writeDigest(file, first)
        else loadExpectedDigest(file)
      assertEquals(first, expected)
    }
  }

  private def bench(file: String): CorpusBench = {
    val bench = new CorpusBench
    bench.name = file
    bench.setup()
    bench
  }

  private def eventDigest(file: String): String = {
    val events = bench(file).fromStringToEvents match {
      case Right(value) => value
      case Left(error)  => fail(s"$file failed to parse: $error")
    }
    val rendered =
      TestRunnerUtils.convertEventToYamlTestSuiteFormat(events.map(_.kind))
    sha256Hex(rendered.getBytes(StandardCharsets.UTF_8))
  }

  private def sha256Hex(bytes: Array[Byte]): String = {
    val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
    val hex    = new StringBuilder(digest.length * 2)
    digest.foreach(b => hex.append(f"${b & 0xff}%02x"))
    hex.result()
  }

  // Returns the digest just written. Reading it back keeps a regenerate run green
  // even though the test classpath still has the previous copy of the resource.
  private def writeDigest(file: String, digest: String): String = {
    val path = digestSourcePath(file)
    Files.createDirectories(path.getParent)
    val writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)
    try writer.write(digest + "\n")
    finally writer.close()
    println(s"regenerated ${path.toAbsolutePath} $digest")
    val reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)
    try reader.readLine().trim
    finally reader.close()
  }

  private def loadExpectedDigest(file: String): String = {
    val resource = s"/linkml/$file.events.sha256"
    val stream   = getClass.getResourceAsStream(resource)
    if (stream == null)
      fail(s"missing $resource; regenerate with sbt -Dregenerate=true 'benchmark/test'")
    Using.resource(stream) { in =>
      scala.io.Source.fromInputStream(in, "UTF-8").mkString.trim
    }
  }

  private def digestSourcePath(file: String): Path =
    repoRoot.resolve(
      Paths.get("benchmark", "src", "test", "resources", "linkml", s"$file.events.sha256")
    )

  private def repoRoot: Path = {
    var dir = Paths.get("").toAbsolutePath
    while (dir != null && !Files.exists(dir.resolve("build.sbt")))
      dir = dir.getParent
    if (dir == null) Paths.get("").toAbsolutePath else dir
  }
}
