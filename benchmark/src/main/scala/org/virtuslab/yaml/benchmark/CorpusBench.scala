package org.virtuslab.yaml.benchmark

import org.virtuslab.yaml.*
import org.openjdk.jmh.annotations.{Benchmark, Param, Setup}
import org.virtuslab.yaml.internal.load.parse.{Event, ParserImpl}
import org.virtuslab.yaml.internal.load.reader.Tokenizer

import scala.compiletime.uninitialized
import scala.io.Source
import scala.util.Using

// Separate from ParsingBench: that bench's fromStringToStruct matches on the
// file name and would throw MatchError for these schemas.
class CorpusBench extends CommonParams {
  @Param(Array("cgmes-core.yml", "cgmes-dynamics.yml", "TC57CIM.yml"))
  var name: String = uninitialized

  private var yaml: String = uninitialized

  @Setup
  def setup(): Unit =
    yaml = Using.resource(getClass.getResourceAsStream(s"/linkml/$name")) { in =>
      Source.fromInputStream(in, "UTF-8").mkString
    }

  @Benchmark
  def fromStringToEvents: Either[YamlError, List[Event]] =
    ParserImpl(Tokenizer.make(yaml)).getEvents()

  @Benchmark
  def fromStringToNode: Either[YamlError, Node] = parseYaml(yaml)
}
