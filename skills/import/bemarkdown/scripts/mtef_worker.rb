#!/usr/bin/env jruby
# frozen_string_literal: true

# Persistent JSONL worker for strict MTEF v3/v5 parsing.  The Python
# controller launches this file through model_runtime.py exec so the worker
# always uses the bundled Temurin/JRuby runtime and the allowlisted parser
# libraries.

require 'digest'
require 'fileutils'
require 'json'
require 'stringio'
require 'mathtype'

def parse_arguments(argv)
  values = {}
  until argv.empty?
    key = argv.shift
    case key
    when '--input', '--output'
      values[key.delete_prefix('--').to_sym] = argv.shift
    when '--max-batch'
      values[:max_batch] = Integer(argv.shift)
    else
      raise ArgumentError, "unknown argument: #{key}"
    end
  end
  values[:max_batch] ||= 512
  raise ArgumentError, '--input is required' unless values[:input]
  raise ArgumentError, '--output is required' unless values[:output]
  raise ArgumentError, '--max-batch must be between 1 and 512' unless (1..512).cover?(values[:max_batch])
  values
end

def parse_one(task)
  path = File.expand_path(String(task.fetch('ole_path')))
  raise IOError, "OLE input is missing: #{path}" unless File.file?(path)

  parser = Mathtype::OleFileParser.new(path)
  payload = parser.equation
  raise IOError, 'Equation Native contains no MTEF payload' unless payload && !payload.empty?

  actual_hash = Digest::SHA256.hexdigest(payload)
  expected_hash = String(task.fetch('mtef_sha256')).downcase
  raise IOError, "MTEF hash mismatch: expected=#{expected_hash} actual=#{actual_hash}" unless actual_hash == expected_hash

  segments = []
  consumed = 0
  while consumed < payload.bytesize
    version = payload.getbyte(consumed)
    raise NotImplementedError, "unsupported MTEF version at byte #{consumed}: #{version}" unless [3, 5].include?(version)
    stream = StringIO.new(payload.byteslice(consumed..-1))
    equation = case version
               when 3 then Mathtype3::Equation.read(stream)
               when 5 then Mathtype5::Equation.read(stream)
               end
    raise IOError, "MTEF segment at byte #{consumed} consumed no data" unless stream.pos.positive?

    converter = Mathtype::Converter.allocate
    converter.instance_variable_set(:@version, version)
    builder = Nokogiri::XML::Builder.new do |xml|
      converter.instance_variable_set(:@xml, xml)
      xml.root { converter.process(object: equation.snapshot) }
    end
    segments << {
      'version' => version,
      'offset' => consumed,
      'bytes' => stream.pos,
      'xml' => builder.to_xml
    }
    consumed += stream.pos
  end

  {
    'ok' => true,
    'id' => task.fetch('id'),
    'mtef_sha256' => actual_hash,
    'version' => segments.first.fetch('version'),
    'segment_count' => segments.length,
    'segments' => segments,
    'total_bytes' => payload.bytesize,
    'consumed_bytes' => consumed,
    'xml' => segments.length == 1 ? segments.first.fetch('xml') : nil
  }
rescue Exception => e # rubocop:disable Lint/RescueException -- NotImplementedError is a hard parse result
  {
    'ok' => false,
    'id' => task['id'],
    'mtef_sha256' => task['mtef_sha256'],
    'error_class' => e.class.name,
    'error' => e.message,
    'backtrace' => Array(e.backtrace).first(8)
  }
end

def atomic_replace(source, destination)
  FileUtils.mkdir_p(File.dirname(destination))
  File.delete(destination) if File.exist?(destination)
  File.rename(source, destination)
end

args = parse_arguments(ARGV)
tasks = File.readlines(args[:input], encoding: 'UTF-8').each_with_object([]) do |line, values|
  stripped = line.strip
  values << JSON.parse(stripped) unless stripped.empty?
end
raise ArgumentError, "batch contains #{tasks.length} items; maximum is #{args[:max_batch]}" if tasks.length > args[:max_batch]

output = File.expand_path(args[:output])
partial = output + '.partial'
FileUtils.mkdir_p(File.dirname(output))
completed = {}
if File.file?(partial)
  File.foreach(partial, encoding: 'UTF-8') do |line|
    begin
      record = JSON.parse(line)
      completed[String(record['id'])] = true if record['id']
    rescue JSON::ParserError
      # A torn final line is ignored and will be recomputed.
    end
  end
end

File.open(partial, 'a:UTF-8') do |handle|
  tasks.each do |task|
    next if completed[String(task['id'])]
    record = parse_one(task)
    handle.write(JSON.generate(record))
    handle.write("\n")
    handle.flush
    handle.fsync
  end
end
atomic_replace(partial, output)
