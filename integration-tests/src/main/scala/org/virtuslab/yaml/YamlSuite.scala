package org.virtuslab.yaml

import java.util.concurrent.Callable
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

import scala.collection.mutable

import org.virtuslab.yaml.internal.load.parse.EventKind
import org.virtuslab.yaml.internal.load.parse.ParserImpl
import org.virtuslab.yaml.internal.load.reader.Tokenizer

/**
 * One case of the vendored yaml-test-suite data release.
 *
 * `id` is the directory name, `2G84/00` for a subcase. `isError` is true when the case has an
 * `error` file: the parser must reject `in.yaml`, and `test.event` is not compared.
 */
final case class YamlSuiteCase(
    id: String,
    name: String,
    tags: List[String],
    inYaml: String,
    expectedEvents: String,
    isError: Boolean
)

object YamlSuiteCase {

  /** Walks `root` (integration-tests/yaml-test-suite), including subcases like `2G84/00`. */
  def loadAll(root: os.Path): List[YamlSuiteCase] = {
    val tags = readTags(root / "tags.txt")
    val caseDirs = os
      .list(root)
      .filter(os.isDir)
      .flatMap { dir =>
        if (os.exists(dir / "in.yaml")) List(dir)
        else os.list(dir).filter(d => os.isDir(d) && os.exists(d / "in.yaml"))
      }
    caseDirs.map { dir =>
      val id = dir.relativeTo(root).segments.mkString("/")
      YamlSuiteCase(
        id = id,
        name = os.read(dir / "===").trim,
        tags = tags.getOrElse(id, Nil),
        inYaml = os.read(dir / "in.yaml"),
        expectedEvents = os.read(dir / "test.event").trim,
        isError = os.exists(dir / "error")
      )
    }.toList
  }

  /** tags.txt: one line per case, `<id> <tag> <tag> ...`. */
  def readTags(file: os.Path): Map[String, List[String]] =
    os.read
      .lines(file)
      .map(_.trim)
      .filter(_.nonEmpty)
      .map { line =>
        val parts = line.split(' ').toList
        parts.head -> parts.tail
      }
      .toMap
}

sealed abstract class YamlSuiteStatus(val name: String)
object YamlSuiteStatus {
  case object Pass            extends YamlSuiteStatus("pass")
  case object EventsDiffer    extends YamlSuiteStatus("events-differ")
  case object WronglyRejected extends YamlSuiteStatus("wrongly-rejected")
  case object WronglyAccepted extends YamlSuiteStatus("wrongly-accepted")
  case object Crash           extends YamlSuiteStatus("crash")

  val all: List[YamlSuiteStatus] = List(Pass, EventsDiffer, WronglyRejected, WronglyAccepted, Crash)
}

final case class YamlSuiteResult(
    testCase: YamlSuiteCase,
    status: YamlSuiteStatus,
    detail: String,
    events: String
)

object YamlSuiteRunner {

  /** A case that runs longer than this is a crash. The worker thread is abandoned. */
  val timeoutSeconds = 10L

  private sealed trait Outcome
  private final case class Parsed(events: List[EventKind])                 extends Outcome
  private final case class Rejected(events: List[EventKind], e: YamlError) extends Outcome

  def runAll(cases: List[YamlSuiteCase]): List[YamlSuiteResult] = {
    val executor = Executors.newCachedThreadPool { (r: Runnable) =>
      val t = new Thread(r, "yaml-suite-case")
      t.setDaemon(true)
      t
    }
    try cases.map(run(_, executor))
    finally executor.shutdownNow()
  }

  private def run(c: YamlSuiteCase, executor: java.util.concurrent.ExecutorService) = {
    val future = executor.submit(new Callable[Outcome] { def call() = parse(c.inYaml) })
    try {
      future.get(timeoutSeconds, TimeUnit.SECONDS) match {
        case Parsed(events)      => classifyParsed(c, events)
        case Rejected(events, e) => classifyRejected(c, events, e)
      }
    } catch {
      case _: TimeoutException =>
        future.cancel(true)
        YamlSuiteResult(c, YamlSuiteStatus.Crash, s"timeout after ${timeoutSeconds}s", "")
      case e: ExecutionException =>
        val cause = e.getCause
        YamlSuiteResult(
          c,
          YamlSuiteStatus.Crash,
          oneLine(s"${cause.getClass.getName}: ${cause.getMessage}"),
          ""
        )
    }
  }

  private def parse(yaml: String): Outcome = {
    val parser          = ParserImpl(Tokenizer.make(yaml))
    val acc             = mutable.ListBuffer.empty[EventKind]
    var result: Outcome = null
    while (result == null) {
      parser.getNextEvent() match {
        case Right(event) =>
          acc += event.kind
          if (event.kind == EventKind.StreamEnd) result = Parsed(acc.toList)
        case Left(error) =>
          result = Rejected(acc.toList, error)
      }
    }
    result
  }

  private def classifyParsed(c: YamlSuiteCase, events: List[EventKind]) = {
    val obtained = TestRunnerUtils.convertEventToYamlTestSuiteFormat(events)
    if (c.isError)
      YamlSuiteResult(c, YamlSuiteStatus.WronglyAccepted, "parsed without error", obtained)
    else if (obtained == c.expectedEvents)
      YamlSuiteResult(c, YamlSuiteStatus.Pass, "", obtained)
    else
      YamlSuiteResult(
        c,
        YamlSuiteStatus.EventsDiffer,
        firstDifference(c.expectedEvents, obtained),
        obtained
      )
  }

  private def classifyRejected(c: YamlSuiteCase, events: List[EventKind], e: YamlError) = {
    val obtained = TestRunnerUtils.convertEventToYamlTestSuiteFormat(events)
    val detail   = oneLine(e.msg)
    if (c.isError) YamlSuiteResult(c, YamlSuiteStatus.Pass, detail, obtained)
    else YamlSuiteResult(c, YamlSuiteStatus.WronglyRejected, detail, obtained)
  }

  def firstDifference(expected: String, obtained: String): String = {
    val exp = expected.linesIterator.toVector
    val obt = obtained.linesIterator.toVector
    val i   = exp.indices.find(i => i >= obt.length || exp(i) != obt(i)).getOrElse(exp.length)
    def at(v: Vector[String]) = if (i < v.length) v(i) else "<end>"
    s"event ${i + 1}: expected ${at(exp)} got ${at(obt)}"
  }

  private def oneLine(s: String): String = {
    val line = Option(s).getOrElse("").linesIterator.map(_.trim).find(_.nonEmpty).getOrElse("")
    if (line.length > 200) line.take(200) + "..." else line
  }

  def toJson(results: List[YamlSuiteResult]): String =
    results
      .map { r =>
        val tags = r.testCase.tags.map(jsonString).mkString("[", ", ", "]")
        s"""  {"id": ${jsonString(r.testCase.id)}, "tags": $tags, "status": ${jsonString(
            r.status.name
          )}, "detail": ${jsonString(r.detail)}}"""
      }
      .mkString("[\n", ",\n", "\n]\n")

  private def jsonString(s: String): String = {
    val sb = new java.lang.StringBuilder("\"")
    s.foreach {
      case '"'          => sb.append("\\\"")
      case '\\'         => sb.append("\\\\")
      case '\n'         => sb.append("\\n")
      case '\r'         => sb.append("\\r")
      case '\t'         => sb.append("\\t")
      case c if c < ' ' => sb.append(f"\\u${c.toInt}%04x")
      case c            => sb.append(c)
    }
    sb.append('"').toString
  }
}
