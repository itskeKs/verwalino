Feature: Organization AI settings
  Each organization brings its own provider key and selects one model for all AI work.

  Scenario: An administrator enables AI with the organization's own key
    Given an administrator of "Verwaltung Alpha" is signed in
    And OpenAI lists two models for the organization's test key
    When the administrator chooses a listed AI model and enables import and questions
    Then the AI settings are saved without returning the key
    And the organization's next AI settings read uses the chosen model


  Scenario: An administrator selects a model from a local compatible provider
    Given an administrator of "Verwaltung Alpha" is signed in
    And a local compatible provider lists a synthetic model
    When the administrator selects that provider and enables imports
    Then the selected endpoint and model are saved without returning the key
