Feature: Organization isolation
  Every organization manages only its own data. A user who is signed in to one
  organization never sees, opens or changes the records of another
  organization, not even when they know the other record's id.

  Background:
    Given the organizations "Verwaltung Alpha" and "Verwaltung Beta"
    And each organization has a property, a person and a bank account of its own
    And an administrator of "Verwaltung Alpha" is signed in

  Scenario: Personal appearance cannot change another organization's users
    Given the organizations have independent personal designs
    When Verwaltung Alpha changes its personal design
    Then Verwaltung Beta keeps its personal design

  Scenario: Another organization cannot read or replace household history
    Given a unit with saved household history belongs to Verwaltung Alpha
    When Verwaltung Beta tries to read or replace that unit's households
    Then the households are not found and Verwaltung Alpha retains its rows

  Scenario: Another organization cannot read or change a commercial preallocation
    Given a shop of 60 square metres and flats of 75 and 65 square metres with annual waste costs of 1200 euros
    When Verwaltung Beta tries to open and calculate Alpha's commercial statement
    Then the commercial statement is not found and Alpha's allocation rule is unchanged

  Scenario: Another organization's WEG financial details look missing
    Given a payment, economic plan and annual WEG settlement belong to Verwaltung Alpha
    When Verwaltung Beta opens those financial details and their input-owner list
    Then the foreign financial records have the same German explanation as missing records

  Scenario: Another organization cannot read an owner's due-date schedule
    Given a payment, economic plan and annual WEG settlement belong to Verwaltung Alpha
    When Verwaltung Beta requests Alpha's owner ledger at a selected due-date cutoff
    Then the foreign owner schedule looks missing and exposes no financial amounts

  Scenario: Another organization's empty annual drafts cannot be deleted
    Given an empty asset report belongs to Verwaltung Alpha
    When Verwaltung Beta tries to delete the empty annual draft
    Then the annual draft is not found and remains available to Verwaltung Alpha

  Scenario: Another organization cannot read a dated rent account or change separate lease advances
    Given a lease with 750 euros rent, 150 euros operating and 90 euros heating advance
    When another organization requests the lease's advance history and statement actions
    Then all separate advance requests are reported as not found

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

  Scenario: Another organization's lease documents cannot be opened or created
    Given a lease with a landlord, a tenant and a unit meter
    When another organization's administrator requests its lease documents
    Then the lease documents are reported as not found

  Scenario Outline: A <record> of another organization cannot be changed
    When the user tries to rename the <record> of "Verwaltung Beta" to "Taken over by Alpha"
    Then the change is refused
    And "Verwaltung Beta" still has its <record> unchanged

    Examples:
      | record       |
      | property     |
      | person       |
      | bank account |

  Scenario: Another organization's settlement period offers are not disclosed
    When the user opens the settlement period offers of "Verwaltung Beta"
    Then the property is reported as not found

  Scenario: An overlong name does not reveal another organization's unit
    Given "Verwaltung Beta" has a unit named "Wohnung 1"
    When the user tries to rename the unit of "Verwaltung Beta" to "Wohnung 1 · Südflügel · Aufgang Änne · GartenblickX"
    Then the unit is reported as not found
    And "Verwaltung Beta" still has its unit unchanged

  Scenario: Invalid area does not reveal another organization's property or unit
    Given "Verwaltung Beta" has a unit named "Area check"
    When the user submits an invalid area for that unit and for a new unit in "Verwaltung Beta"
    Then neither attempt reveals the foreign property or unit
    And "Verwaltung Beta" still has its unit unchanged

  Scenario: A duplicate number does not reveal another organization's units
    Given "Verwaltung Beta" has a unit named "Wohnung 2"
    And "Verwaltung Beta" has a unit named "Wohnung 3"
    When the user tries to rename the unit of "Verwaltung Beta" to "Wohnung 2"
    Then the unit is reported as not found
    When the user tries to add or import another "Wohnung 2" into the property of "Verwaltung Beta"
    Then the property is reported as not found for both attempts
    And "Verwaltung Beta" still has its unit unchanged

  Scenario: Signing in again keeps the session in the same organization
    When the administrator signs in again
    Then the signed-in session belongs to "Verwaltung Alpha"
    When the user lists the properties
    Then the list shows the properties of "Verwaltung Alpha"
    And the list does not show the properties of "Verwaltung Beta"

  Scenario Outline: Another organization's Anlage V <view> is not accessible
    When the user opens the Anlage V <view> for the property of "Verwaltung Beta"
    Then the report is reported as not found

    Examples:
      | view     |
      | summary  |
      | download |

  Scenario: Administrators share technical diagnostics without organization records
    When the administrator downloads a diagnostics bundle
    Then the bundle contains technical counts without either organization’s records

  Scenario: A password recovery link changes only the mailbox owner's account
    Given a user who is signed in
    And a test mailbox for password recovery
    When the user requests a password reset link
    And the user submits the captured recovery link
    Then the password reset is accepted without signing in
    And the old session is no longer valid
    And the other organization's administrator remains signed in

  Scenario: An organization archive cannot be downloaded by another organization
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    Then the archive contains its rental records and the unchanged original
    And the archive contains no credentials or data of the other organization

  Scenario: Import and deletion cancellation affect only the destination organization
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization
    And the destination administrator confirms and revokes deletion
    Then the source organization still has its original records and session

  Scenario: Another organization's reserve contribution correction cannot be reduced or deleted
    Given an audited draft reserve contribution correction belongs to Verwaltung Alpha
    When Verwaltung Beta tries to reduce and delete that contribution correction
    Then the reserve contribution correction is not found and remains unchanged

  Scenario: Another organization's special levy authority cannot be read or linked
    Given Gartenstraße 5 has two owners and an adopted roof repair resolution
    When the manager creates the roof repair levy due on 1 July 2026
    And Verwaltung Beta tries to create, read and link that special levy
    Then the special levy authority is not found and its frozen obligation is unchanged

  Scenario: A bank debit cannot cover another organization's cost
    Given each organization has a confirmed cost and an outgoing bank debit
    When Verwaltung Alpha tries to assign its debit to Verwaltung Beta's cost
    Then the foreign cost is not found and both debits remain new

  Scenario: A foreign owner looks missing when recording or assigning rent
    Given a rental receipt and an owner for the rented unit
    And Verwaltung Beta has an owner profile
    When Verwaltung Alpha tries to record and assign rent to the foreign owner and to an unknown owner
    Then both routes report the owners as equally missing
    And the rental receipt remains pending with no payment recorded

  Scenario: Reminder policy belongs to the signed-in organization
    When Verwaltung Alpha saves its own dunning settings
    And Verwaltung Beta saves different dunning settings
    Then each administrator reads only their own dunning settings
