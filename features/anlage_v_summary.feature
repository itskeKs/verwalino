Feature: Anlage V summary
  A landlord can use the property year overview as a mapping aid for Anlage V.
  Income and expenses are assigned transparently and reconcile to the overview.

  Scenario: A landlord sees the year's income and expenses sorted into Anlage V
    Given a rental property with booked rent, advances and confirmed water costs
    When the landlord opens the Anlage V summary for that year
    Then rent, advances and water costs appear on their Anlage V lines
    And mapped and unassigned amounts match the property year overview

  Scenario: A deposit stays visible without increasing income and a tenant backpayment counts as levies
    Given a rental property with booked rent, advances and confirmed water costs
    And a booked deposit of 2250 euros and a tenant backpayment of 75 euros
    When the landlord opens the Anlage V summary for that year
    Then the year overview and CSV show 1459.56 euros income excluding the deposit
    And the deposit stays separate and the backpayment is included in levies
    And mapped and unassigned amounts match the property year overview

  @browser
  Scenario: The landlord reads the mapping on wide and narrow screens and downloads it
    Given a rental property with booked rent, advances and confirmed water costs
    When the landlord displays the property year overview in the browser
    Then the Anlage V mapping and reconciliation are readable on wide and narrow screens
    And the landlord can download the Anlage V CSV
