Feature: A manual economic plan keeps its calculated inputs
  Reopening the draft keeps the calculated costs and zero reserve available for finalization.

  @browser
  Scenario Outline: Reopening a manual plan keeps the costs used for finalization
    Given a property with one owner responsible for the full WEG costs
    When the manager creates a zero-reserve economic plan and calculates 7200 euros in annual costs at <width> pixels
    Then the plan shows a 600 euro monthly advance and zero reserve
    When the manager cancels finalization and reopens the draft from the list
    Then the editable annual cost is still 7200 euros and the monthly advance is still 600 euros
    When the manager confirms the data basis again and finalizes the plan
    Then the saved plan shows a 7200 euro annual advance and a 600 euro monthly advance

    Examples:
      | width |
      | 1280  |
      | 360   |
