package org.virtuslab.yaml

import scala.annotation.tailrec
import scala.collection.mutable

import org.virtuslab.yaml.Tag
import org.virtuslab.yaml.internal.load.parse.Anchor
import org.virtuslab.yaml.internal.load.parse.EventKind
import org.virtuslab.yaml.internal.load.parse.EventKind._
import org.virtuslab.yaml.internal.load.parse.NodeEventMetadata
import org.virtuslab.yaml.internal.load.parse.ParserImpl
import org.virtuslab.yaml.internal.load.reader.token.ScalarStyle
import org.virtuslab.yaml.internal.load.reader.Tokenizer

trait TestRunner {
  def inYaml: String
  def expectedEvents: String

  def run(): RunnerResult = {
    val reader = Tokenizer.make(inYaml)
    val parser = ParserImpl(reader)
    val acc    = new mutable.ArrayDeque[EventKind]()

    @tailrec
    def loop(): RunnerResult = {
      parser.getNextEvent() match {
        case Right(event) =>
          acc.append(event.kind)
          if (event.kind != EventKind.StreamEnd) loop()
          else RunnerResult(acc.toList, expectedEvents)
        case Left(error) =>
          RunnerResult(acc.toList, expectedEvents, error)
      }
    }
    loop()
  }

}

/**
 * Renders events in the yaml-test-suite `test.event` format.
 *
 * Scalar values are escaped as test.event escapes them: `\` `\b` `\n` `\r` `\t`.
 * Flow collections carry the `{}` / `[]` marker.
 */
object TestRunnerUtils {

  implicit class OptionAnchorOps(anchor: Option[Anchor]) {
    def anchorAsString: String = anchor.map(a => s" &${a.anchor}").getOrElse("")
  }
  implicit class OptionTagOps(tag: Option[Tag]) {
    def tagAsString: String = tag.map(t => s" <${t.value}>").getOrElse("")
  }
  implicit class NodeEventMetadataOps(metadata: NodeEventMetadata) {
    def asString: String =
      List(
        metadata.anchor.anchorAsString,
        metadata.tag.tagAsString
      ).mkString
  }

  def escapeValue(value: String): String = {
    val sb = new java.lang.StringBuilder(value.length)
    value.foreach {
      case '\\' => sb.append("\\\\")
      case '\b' => sb.append("\\b")
      case '\n' => sb.append("\\n")
      case '\r' => sb.append("\\r")
      case '\t' => sb.append("\\t")
      case c    => sb.append(c)
    }
    sb.toString
  }

  def convertEventToYamlTestSuiteFormat(events: Seq[EventKind]): String =
    events
      .map {
        case StreamStart               => "+STR"
        case StreamEnd                 => "-STR"
        case DocumentStart(explicit)   => if (explicit) "+DOC ---" else "+DOC"
        case DocumentEnd(explicit)     => if (explicit) "-DOC ..." else "-DOC"
        case SequenceStart(data, flow) => s"+SEQ${if (flow) " []" else ""}${data.asString}"
        case SequenceEnd               => "-SEQ"
        case MappingStart(data, flow)  => s"+MAP${if (flow) " {}" else ""}${data.asString}"
        case MappingEnd                => "-MAP"
        case Alias(alias)              => s"=ALI *${alias.anchor}"
        case Scalar(value, style, data) =>
          val v = escapeValue(value)
          style match {
            case ScalarStyle.Plain        => s"=VAL${data.asString} :$v"
            case ScalarStyle.DoubleQuoted => s"""=VAL${data.asString} "$v"""
            case ScalarStyle.SingleQuoted => s"=VAL${data.asString} '$v"
            case ScalarStyle.Folded       => s"=VAL${data.asString} >$v"
            case ScalarStyle.Literal      => s"=VAL${data.asString} |$v"
          }
      }
      .mkString("\n")

}

case class K8sYamlTestRunner(yamlPath: os.Path, libYaml: os.Path) extends TestRunner {
  override val inYaml = os.read(yamlPath)
  override val expectedEvents = os
    .proc(libYaml, yamlPath)
    .call(cwd = os.pwd)
    .out
    .text()
    .trim

  override def run(): RunnerResult = {
    println(yamlPath)
    super.run()
  }

}
