# agentic-vault:adapter ps-parse engine=0.32.0
<#
  ps-parse.ps1 - parse one PowerShell command for the opt-in deny-zone guard
  (deny-guard.py) without running it. ASCII only, so Windows PowerShell 5.1
  reads it correctly without a byte order mark. The plugin ships it next to
  deny-guard.py in assets/adapters/deny-guard/; a user who turns the guard on
  copies both files to 00-meta/scripts/ (guide.md). The guard starts it only
  when the command names a deny zone or a guard control:
    powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ps-parse.ps1

  Contract:
    - Input: the command text on stdin, read as UTF-8 (the console code page
      would break non-ASCII text).
    - It calls [System.Management.Automation.Language.Parser]::ParseInput and
      walks the syntax tree. Nothing in the command is run, expanded or
      resolved.
    - Output: one line of JSON on stdout and exit 0. Every character outside
      printable ASCII is written as a \uXXXX escape, so no code page can change
      the line. Keys:
        errors              number of parse errors
        commands            command names in source order; null when the command
                            element is not a constant name (& { ... }, & $x)
        strings             the value of every constant string: bare words,
                            quoted strings and here-strings, command names too
        expandable          number of expandable strings ("...$x...")
        expandable_strings  their text with variables and $(...) as written
        flags               sorted names of constructs PowerShell evaluates when
                            the command runs:
          invoke_expression   Invoke-Expression or iex
          start_process       Start-Process, start or saps
          invoke_command      Invoke-Command or icm
          script_block_call   & or . that runs a script block or another value
                              that is not a constant name (& { ... }, & $x)
          dot_source          . that runs a script or block in the caller's scope
          encoded_command     a parameter named -EncodedCommand, -ec or a shorter
                              prefix (-enc, -e)
          provider_path       a path for a provider other than FileSystem
                              (Registry::, Env:, HKLM:, any drive name longer than
                              one letter) or a drive-qualified variable (${C:\x},
                              $env:X)
          expression          any other computed part: variables other than
                              $true, $false and $null, operators, method calls and
                              property access, sub-expressions, script blocks,
                              assignments, loops and definitions
    - Exit 1 with one line on stderr when the input cannot be read or parsed at
      all. The guard asks the user unless it gets exit 0 and this JSON.
#>
$ErrorActionPreference = 'Stop'

# Syntax tree nodes that only hold literal command text. Every other node type
# is computed when the command runs and sets the "expression" flag.
$LiteralNodeTypes = @(
    'ScriptBlockAst', 'NamedBlockAst', 'StatementBlockAst', 'PipelineAst', 'PipelineChainAst',
    'CommandAst', 'CommandParameterAst', 'CommandExpressionAst', 'StringConstantExpressionAst',
    'ExpandableStringExpressionAst', 'ConstantExpressionAst', 'ArrayLiteralAst',
    'FileRedirectionAst', 'MergingRedirectionAst', 'ErrorStatementAst', 'ErrorExpressionAst'
)
# Command names (the part after a module qualifier) that run code or start
# processes the guard cannot read. Hashtable keys ignore case.
$CommandFlags = @{
    'iex' = 'invoke_expression'; 'invoke-expression' = 'invoke_expression'
    'start' = 'start_process'; 'saps' = 'start_process'; 'start-process' = 'start_process'
    'icm' = 'invoke_command'; 'invoke-command' = 'invoke_command'
}

# Provider::path for a provider other than FileSystem (with or without a
# module name), or a drive name longer than one letter (HKLM:, Env:, a
# PSDrive) not followed by a space, which text such as "note: x" is. A $
# starts a variable, not a drive: "$env:X\a" is not a path here (the variable
# itself sets the flag). deny-guard.py repeats this test.
function Test-ProviderPath([string]$Text) {
    $trimmed = $Text.TrimStart()   # not $text: PowerShell variable names ignore case
    if ($trimmed -match '^(?:[^\s:\\/$]+\\)?([^\s:\\/$]+)::') { return ($Matches[1] -ne 'FileSystem') }
    return ($trimmed -match '^[^\s:\\/$]{2,}:(?!\s)')
}

function ConvertTo-JsonText($Value) {
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [string]) {
        # \z, not $: $ also matches before a final newline, which must be escaped.
        if ($Value -cmatch '^[\x20\x21\x23-\x5b\x5d-\x7e]*\z') { return '"' + $Value + '"' }
        $builder = New-Object System.Text.StringBuilder
        [void]$builder.Append('"')
        foreach ($char in $Value.ToCharArray()) {
            $code = [int]$char
            if ($code -eq 34) { [void]$builder.Append('\"') }
            elseif ($code -eq 92) { [void]$builder.Append('\\') }
            elseif ($code -lt 32 -or $code -gt 126) { [void]$builder.Append(('\u{0:x4}' -f $code)) }
            else { [void]$builder.Append($char) }
        }
        [void]$builder.Append('"')
        return $builder.ToString()
    }
    if ($Value -is [int]) { return [string]$Value }
    $items = New-Object 'System.Collections.Generic.List[string]'
    foreach ($item in $Value) { $items.Add((ConvertTo-JsonText $item)) }
    return '[' + [string]::Join(',', $items) + ']'
}

try {
    $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding($false)))
    $command = $reader.ReadToEnd()
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($command, [ref]$tokens, [ref]$parseErrors)

    $commands = New-Object System.Collections.ArrayList
    $strings = New-Object System.Collections.ArrayList
    $expandable = New-Object System.Collections.ArrayList
    $flags = @{}
    foreach ($node in $ast.FindAll({ $true }, $true)) {
        $type = $node.GetType().Name
        if ($type -eq 'CommandAst') {
            $name = $node.GetCommandName()
            [void]$commands.Add($name)
            if ($name) {
                $flag = $CommandFlags[($name -split '[\\/]')[-1]]
                if ($flag) { $flags[$flag] = $true }
            }
            $operator = [string]$node.InvocationOperator
            if ($operator -eq 'Ampersand' -or $operator -eq 'Dot') {
                if (-not ($node.CommandElements[0] -is [System.Management.Automation.Language.StringConstantExpressionAst])) {
                    $flags['script_block_call'] = $true
                }
                if ($operator -eq 'Dot') { $flags['dot_source'] = $true }
            }
        } elseif ($type -eq 'CommandParameterAst') {
            $parameter = $node.ParameterName.ToLowerInvariant()
            if ($parameter -eq 'ec' -or ($parameter.Length -gt 0 -and
                    'encodedcommand'.StartsWith($parameter, [System.StringComparison]::Ordinal))) {
                $flags['encoded_command'] = $true
            }
        } elseif ($type -eq 'StringConstantExpressionAst') {
            [void]$strings.Add($node.Value)
            if (Test-ProviderPath $node.Value) { $flags['provider_path'] = $true }
        } elseif ($type -eq 'ExpandableStringExpressionAst') {
            [void]$expandable.Add($node.Value)
            if (Test-ProviderPath $node.Value) { $flags['provider_path'] = $true }
        } elseif ($type -eq 'VariableExpressionAst') {
            $path = $node.VariablePath
            if ($path.IsDriveQualified) { $flags['provider_path'] = $true }
            if (@('true', 'false', 'null') -notcontains $path.UserPath) { $flags['expression'] = $true }
        } elseif ($LiteralNodeTypes -notcontains $type) {
            $flags['expression'] = $true
        }
    }
    $flagNames = New-Object 'System.Collections.Generic.List[string]'
    foreach ($key in ($flags.Keys | Sort-Object)) { $flagNames.Add([string]$key) }

    $json = '{"errors":' + [int]$parseErrors.Count +
        ',"commands":' + (ConvertTo-JsonText $commands) +
        ',"strings":' + (ConvertTo-JsonText $strings) +
        ',"expandable":' + [int]$expandable.Count +
        ',"expandable_strings":' + (ConvertTo-JsonText $expandable) +
        ',"flags":' + (ConvertTo-JsonText $flagNames) + '}'
    $stdout = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), (New-Object System.Text.UTF8Encoding($false)))
    $stdout.Write($json + "`n")
    $stdout.Flush()
} catch {
    [Console]::Error.WriteLine('ps-parse: ' + $_.Exception.GetType().Name + ': ' + $_.Exception.Message)
    exit 1
}
exit 0
