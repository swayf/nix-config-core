# Source paths only. Host HM and persistent container HOME use the same scripts.
{
  askUserQuestion = ./install-ask-user-question.sh;
  contextUsage = ./install-context-usage.sh;
  subagents = ./install-subagents.py;
  webAccess = ./install-web-access.py;
  sharedModels = ./shared-models.py;
  disableBell = ./disable-bell.py;
  packetReviewer = ./install-packet-reviewer.sh;
  bell = ./install-bell.sh;
  statusline = ./install-statusline.sh;
  compactPrompts = ./install-compact-prompts.sh;
  compactTools = ./install-compact-tools.sh;
  modelPresets = ./install-model-presets.sh;
  brainstorming = ./install-brainstorming.sh;
  lazyCapabilities = ./install-lazy-capabilities.sh;
  wikiAsk = ./install-wiki-ask.sh;
  contextHandoff = ./install-context-handoff.sh;
}
