Feature: A new economic plan keeps its reserve
  The editor starts with the reserve shown in the draft summary, including zero.

  @browser
  Scenario Outline: A zero-reserve plan calculates monthly advances without retyping zero
    Given a property with one owner responsible for the full WEG costs
    When the manager creates a zero-reserve economic plan and calculates 7200 euros in annual costs at <width> pixels
    Then the plan shows a 600 euro monthly advance and zero reserve

    Examples:
      | width |
      | 1280  |
      | 360   |
