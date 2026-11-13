Feature: Organization AI settings
  Each organization brings its own provider key and selects one model for all AI work.

  Scenario: An administrator enables AI with the organization's own key
    Given an administrator of "Verwaltung Alpha" is signed in
    And OpenAI lists two models for the organization's test key
    When the administrator chooses a listed AI model and confirms the provider
    Then the AI settings are saved without returning the key
    And the organization's next AI settings read uses the chosen model


  Scenario: An administrator selects a model from a compatible provider
    Given an administrator of "Verwaltung Alpha" is signed in
    And a compatible provider lists a synthetic model
    When the administrator selects that provider and confirms it
    Then the selected endpoint and model are saved without returning the key


  Scenario: Storing a key without the provider confirmation is refused
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator saves an AI key without the provider confirmation
    Then the save is refused because the provider confirmation is missing
    And no key is stored


  Scenario: The organization confirms its provider before the key is stored
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator saves an AI key with the provider confirmation
    Then the key is stored and the settings name the confirmer and time


  Scenario: A stored key without a confirmation keeps the AI functions off
    Given an administrator of "Verwaltung Alpha" is signed in
    And OpenAI lists two models for the organization's test key
    And the organization has a stored AI key without a recorded confirmation
    Then the AI settings require the confirmation and the stored key stays unusable
    When the administrator confirms the provider for the stored key
    Then the stored key becomes usable and names the confirmer


  Scenario: Replacing the stored key requires a new confirmation
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has a stored AI key without a recorded confirmation
    When the administrator replaces the stored key without the confirmation
    Then the save is refused because the provider confirmation is missing
    And the previously stored key is unchanged


  Scenario: Removing the key ends the instruction and keeps the record
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator saves an AI key with the provider confirmation
    And the administrator removes the saved key
    Then the key is gone, the functions are off and the confirmation record stays


  Scenario: Key and switch changes are audited without the key
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator saves an AI key and enables declaration import
    And the administrator removes the saved key and switches the AI features off
    Then the new audit events record both changes without the key
