Feature: Organization isolation
  Every organization manages only its own data. A user who is signed in to one
  organization never sees, opens or changes the records of another
  organization, not even when they know the other record's id.

  Background:
    Given the organizations "Verwaltung Alpha" and "Verwaltung Beta"
    And each organization has a property, a person and a bank account of its own
    And an administrator of "Verwaltung Alpha" is signed in

  Scenario Outline: A user sees only the <records> of their own organization
    When the user lists the <records>
    Then the list shows the <records> of "Verwaltung Alpha"
    And the list does not show the <records> of "Verwaltung Beta"

    Examples:
      | records       |
      | properties    |
      | persons       |
      | bank accounts |

  Scenario Outline: A <record> of another organization cannot be opened by its id
    When the user opens the <record> of "Verwaltung Beta" by its id
    Then the <record> is reported as not found

    Examples:
      | record       |
      | property     |
      | person       |
      | bank account |

  Scenario Outline: A <record> of another organization cannot be changed
    When the user tries to rename the <record> of "Verwaltung Beta" to "Taken over by Alpha"
    Then the change is refused
    And "Verwaltung Beta" still has its <record> unchanged

    Examples:
      | record       |
      | property     |
      | person       |
      | bank account |

  Scenario: Signing in again keeps the session in the same organization
    When the administrator signs in again
    Then the signed-in session belongs to "Verwaltung Alpha"
    When the user lists the properties
    Then the list shows the properties of "Verwaltung Alpha"
    And the list does not show the properties of "Verwaltung Beta"

  Scenario: Administrators share technical diagnostics without organization records
    When the administrator downloads a diagnostics bundle
    Then the bundle contains technical counts without either organization’s records
